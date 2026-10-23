import asyncio
import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from oppo_health.app import create_app


def test_web_auth_and_read_only_mcp(settings, fake_legacy):
    app = create_app(settings, fake_legacy)
    with TestClient(app, base_url=settings.public_base_url) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "OPPO 健康云中转" in page.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").json()["status"] == "ready"
        assert client.get("/api/metrics").status_code == 401
        assert client.get("/mcp/").status_code == 401

        login = client.post("/api/login", json={"username": "admin", "password": "bad"})
        assert login.status_code == 401
        login = client.post("/api/login", json={"username": "admin", "password": "secret"})
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=strict" in login.headers["set-cookie"].lower()
        assert client.get("/api/metrics").status_code == 200
        metric_payload = client.get("/api/metrics").json()
        assert len(metric_payload["metrics"]) == 64
        assert not {"stress_detail", "stress_stat", "stress"} & {item["key"] for item in metric_payload["metrics"]}
        assert client.post("/api/sync", json={"metrics": ["steps"]}).status_code == 403
        queued = client.post("/api/sync", json={"metrics": ["steps"]}, headers={"X-CSRF-Token": csrf})
        assert queued.status_code == 202
        duplicate = client.post("/api/sync", json={"metrics": ["steps"]}, headers={"X-CSRF-Token": csrf})
        assert duplicate.status_code == 202
        assert duplicate.json()["job_id"] == queued.json()["job_id"]
        job_id = queued.json()["job_id"]
        for _ in range(50):
            job = client.get(f"/api/sync/jobs/{job_id}").json()
            if job["status"] in {"success", "partial", "failed"}:
                break
            time.sleep(.02)
        assert job["status"] == "success"
        assert fake_legacy.calls == [("steps", 7)]
        assert client.get("/api/records", params={"metric_key": "steps"}).json()["records"][0]["value"] == 321

        tools = asyncio.run(app.state.mcp.list_tools())
        names = {tool.name for tool in tools}
        assert names == {"get_health_overview", "query_health_data", "list_health_metrics", "get_sync_status"}
        assert all(tool.annotations.readOnlyHint is True for tool in tools)
        before = len(fake_legacy.calls)
        result = asyncio.run(app.state.mcp.call_tool("get_health_overview", {"days": 7}))
        assert result
        assert len(fake_legacy.calls) == before

        assert client.get("/mcp/.well-known/oauth-authorization-server").status_code == 200
        registration = client.post("/mcp/register", json={
            "redirect_uris": ["http://localhost/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_name": "test-client",
        })
        assert registration.status_code == 201
        oauth_client = registration.json()
        verifier = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-._~"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        authorize = client.get("/mcp/authorize", params={
            "response_type": "code",
            "client_id": oauth_client["client_id"],
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "health:read",
            "state": "state-1",
            "resource": settings.oauth_issuer_url,
        }, follow_redirects=False)
        consent_url = authorize.headers["location"].replace("http://127.0.0.1:8000", "")
        consent = client.get(consent_url, follow_redirects=False)
        assert consent.status_code == 200
        request_id = urlparse(consent_url).path.rsplit("/", 1)[-1]
        approved = client.post(f"/mcp/consent/{request_id}", data={"csrf": csrf, "decision": "approve"}, follow_redirects=False)
        callback = parse_qs(urlparse(approved.headers["location"]).query)
        token_response = client.post("/mcp/token", data={
            "grant_type": "authorization_code",
            "client_id": oauth_client["client_id"],
            "code": callback["code"][0],
            "redirect_uri": "http://localhost/callback",
            "code_verifier": verifier,
            "resource": settings.oauth_issuer_url,
        })
        assert token_response.status_code == 200, token_response.text
        token = token_response.json()["access_token"]
        access = asyncio.run(app.state.oauth_provider.load_access_token(token))
        assert access is not None
        import sqlite3
        with sqlite3.connect(settings.database_path) as con:
            stored = con.execute("SELECT token_json FROM oauth_access_tokens WHERE token_hash != ?", ("",)).fetchall()
        assert all(token not in row[0] for row in stored)
        assert access.scopes == ["health:read"]
        mcp_init = client.post(
            "/mcp/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        assert mcp_init.status_code == 200, mcp_init.text
        assert "OPPO 健康缓存" in mcp_init.text
        mcp_tools = client.post(
            "/mcp/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert mcp_tools.status_code == 200, mcp_tools.text
        assert all(name in mcp_tools.text for name in ["get_health_overview", "query_health_data", "list_health_metrics", "get_sync_status"])
        before_mcp_call = len(fake_legacy.calls)
        mcp_result = client.post(
            "/mcp/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_health_overview", "arguments": {"days": 7}},
            },
        )
        assert mcp_result.status_code == 200, mcp_result.text
        assert "records_in_window" in mcp_result.text
        assert len(fake_legacy.calls) == before_mcp_call
        asyncio.run(app.state.oauth_provider.revoke_token(access))
        assert asyncio.run(app.state.oauth_provider.load_access_token(token)) is None
        client.post("/api/logout", headers={"X-CSRF-Token": csrf})
        assert client.get("/api/metrics").status_code == 401
