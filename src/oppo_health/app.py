"""FastAPI dashboard/API entry point and mounted Streamable HTTP MCP."""
from __future__ import annotations

import asyncio
import html
import importlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote
from uuid import uuid4

from argon2 import extract_parameters
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .auth import LoginRateLimiter, SessionStore, session_from_request, verify_password
from .catalog import COMPOSITES, build_catalog, category_names
from .config import Settings, load_settings
from .mcp_server import build_mcp
from .storage import Repository
from .sync import SyncService

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")
COOKIE_NAME = "oppo_session"
CSRF_HEADER = "x-csrf-token"


def _validate_production_settings(settings: Settings) -> None:
    invalid = []
    if not settings.public_base_url.startswith("https://"):
        invalid.append("PUBLIC_BASE_URL")
    if not settings.oauth_issuer_url.startswith("https://"):
        invalid.append("OAUTH_ISSUER_URL")
    if not settings.secure_cookies:
        invalid.append("SECURE_COOKIES")
    if not settings.web_username or not settings.web_password_hash:
        invalid.append("WEB_USERNAME/WEB_PASSWORD_HASH")
    else:
        try:
            extract_parameters(settings.web_password_hash)
        except (ValueError, TypeError):
            invalid.append("WEB_PASSWORD_HASH")
    if invalid:
        raise RuntimeError("Invalid production security configuration: " + ", ".join(invalid))


def _json_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def _session_or_401(request: Request):
    session = session_from_request(request, request.app.state.sessions)
    if session is None:
        raise HTTPException(status_code=401, detail="LOGIN_REQUIRED")
    return session


def _check_csrf(request: Request, session) -> None:
    token = request.headers.get(CSRF_HEADER)
    if not token or not secrets.compare_digest(token, session.csrf):
        raise HTTPException(status_code=403, detail="CSRF_FAILED")


def _parse_metrics(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    values = [value.strip() for value in raw.split(",") if value.strip()]
    return list(dict.fromkeys(values)) or None


def _validate_metrics(catalog: dict, metrics: list[str] | None) -> None:
    if metrics:
        unknown = [key for key in metrics if key not in catalog]
        if unknown:
            raise HTTPException(status_code=400, detail=f"METRIC_NOT_FOUND:{unknown[0]}")


def _consent_html(pending: dict, csrf: str) -> str:
    name = html.escape(str(pending["client_name"]))
    scopes = "、".join(html.escape(str(scope)) for scope in pending["scopes"])
    request_id = html.escape(str(pending["request_id"]))
    csrf_value = html.escape(csrf)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>授权健康数据读取</title><style>
body{{margin:0;background:#f4f7f6;color:#17302a;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;min-height:100vh}}
main{{width:min(560px,calc(100% - 40px));background:#fff;border:1px solid #dce9e3;border-radius:20px;padding:32px;box-shadow:0 18px 60px #17302a14}}
h1{{margin:0 0 12px;font-size:26px}}p{{line-height:1.7;color:#5a7069}}.scope{{display:inline-block;background:#e6f4ef;border-radius:999px;padding:8px 12px;color:#19705a;font-weight:700}}
.actions{{display:flex;gap:12px;margin-top:28px}}button{{border:0;border-radius:12px;padding:12px 18px;font-weight:700;cursor:pointer}}.approve{{background:#1c8c6b;color:#fff}}.deny{{background:#edf2f0;color:#45615a}}
</style></head><body><main><div style="font-size:14px;color:#1c8c6b;font-weight:800">OPPO 健康云中转</div>
<h1>允许读取健康缓存？</h1><p><strong>{name}</strong> 请求通过 MCP 读取服务器上的个人健康缓存。授权只包含只读权限，不会触发同步或修改数据。</p>
<p>请求权限：<span class="scope">{scopes}</span></p>
<form method="post" action="/mcp/consent/{request_id}"><input type="hidden" name="csrf" value="{csrf_value}">
<div class="actions"><button class="approve" name="decision" value="approve">允许读取</button><button class="deny" name="decision" value="deny">拒绝</button></div></form></main></body></html>"""


def create_app(settings: Settings | None = None, legacy_module=None) -> FastAPI:
    settings = settings or load_settings()
    legacy = legacy_module or importlib.import_module("cloud_mcp")
    catalog = build_catalog(legacy.MCP_METRIC_CHOICES)
    repository = Repository(settings.database_path, legacy, catalog, settings.legacy_database_path)
    synchronizer = SyncService(repository, legacy, settings, catalog)
    mcp, oauth_provider, issuer_url = build_mcp(repository, settings)
    mcp_http = mcp.streamable_http_app()
    sessions = SessionStore(settings.session_ttl_seconds)
    login_limiter = LoginRateLimiter()
    stop_event = asyncio.Event()
    scheduler_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal scheduler_task
        if settings.app_env == "production":
            _validate_production_settings(settings)
        # FastMCP creates a session manager lazily; its lifespan must be entered
        # even in stateless mode so requests can be served and shut down cleanly.
        async with mcp_http.router.lifespan_context(mcp_http):
            if settings.app_env != "test":
                scheduler_task = asyncio.create_task(synchronizer.scheduler(stop_event), name="oppo-health-scheduler")
            try:
                yield
            finally:
                stop_event.set()
                if scheduler_task:
                    scheduler_task.cancel()
                    try:
                        await scheduler_task
                    except asyncio.CancelledError:
                        pass
                await synchronizer.shutdown()
                synchronizer.close()

    app = FastAPI(title="OPPO 健康云中转", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.catalog = catalog
    app.state.repository = repository
    app.state.synchronizer = synchronizer
    app.state.sessions = sessions
    app.state.login_limiter = login_limiter
    app.state.oauth_provider = oauth_provider
    app.state.mcp = mcp
    app.state.mcp_http = mcp_http
    app.state.mcp_issuer_url = issuer_url
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/login", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        checks = {"database": False, "web_auth": bool(settings.web_password_hash), "oppo_auth": False, "public_https": True}
        try:
            with repository.connect() as con:
                con.execute("SELECT 1 FROM health_records LIMIT 1").fetchone()
            checks["database"] = True
            checks["oppo_auth"] = synchronizer._check_auth_file()
            if settings.app_env == "production":
                checks["public_https"] = bool(settings.public_base_url.startswith("https://") and settings.oauth_issuer_url.startswith("https://"))
            ready = all(checks.values())
            return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks}, status_code=200 if ready else 503)
        except Exception as exc:
            log.exception("readiness check failed")
            return JSONResponse({"status": "not_ready", "checks": checks, "error": type(exc).__name__}, status_code=503)

    @app.get("/api/session")
    async def api_session(request: Request) -> dict:
        session = session_from_request(request, sessions)
        if not session:
            return {"authenticated": False}
        return {"authenticated": True, "username": session.username, "csrf_token": session.csrf, "expires_at": session.expires_at}

    @app.post("/api/login")
    async def api_login(request: Request) -> JSONResponse:
        if not settings.web_password_hash:
            return _json_error("LOGIN_NOT_CONFIGURED", "尚未配置网页登录密码哈希", 503)
        try:
            body = await request.json()
        except Exception:
            return _json_error("INVALID_JSON", "请求体必须是 JSON", 400)
        if not isinstance(body, dict):
            return _json_error("INVALID_JSON", "请求体必须是 JSON 对象", 400)
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        client_key = request.client.host if request.client else "unknown"
        if not login_limiter.allowed(client_key):
            retry = login_limiter.retry_after(client_key)
            response = _json_error("RATE_LIMITED", "登录失败次数过多，请稍后重试", 429)
            response.headers["Retry-After"] = str(retry)
            return response
        password_valid = verify_password(password, settings.web_password_hash)
        username_valid = secrets.compare_digest(username, settings.web_username)
        if not username_valid or not password_valid:
            login_limiter.failed(client_key)
            return _json_error("INVALID_CREDENTIALS", "用户名或密码错误", 401)
        login_limiter.succeeded(client_key)
        session = sessions.create(username)
        response = JSONResponse({"ok": True, "username": session.username, "csrf_token": session.csrf})
        response.set_cookie(
            COOKIE_NAME,
            session.token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/api/logout")
    async def api_logout(request: Request) -> JSONResponse:
        session = session_from_request(request, sessions)
        if session:
            _check_csrf(request, session)
            sessions.revoke(session.token)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/health/overview")
    async def api_overview(request: Request, days: int = Query(7, ge=1, le=365)) -> dict:
        _session_or_401(request)
        try:
            result = repository.overview(days)
            result["sync"]["interval_minutes"] = settings.sync_interval_minutes
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/metrics")
    async def api_metrics(request: Request, category: str | None = None) -> dict:
        _session_or_401(request)
        if category and category not in category_names():
            raise HTTPException(status_code=400, detail="INVALID_CATEGORY")
        return {"metrics": repository.catalog_rows(category=category), "categories": category_names()}

    @app.get("/api/records")
    async def api_records(
        request: Request,
        metric_key: str | None = None,
        metrics: str | None = None,
        category: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page_size: int = Query(100, ge=1, le=500),
        cursor: str | None = None,
    ) -> dict:
        _session_or_401(request)
        metric_list = _parse_metrics(metrics)
        _validate_metrics(catalog, [metric_key] if metric_key else None)
        _validate_metrics(catalog, metric_list)
        try:
            return repository.query_records(metric_key, metrics=metric_list, category=category, start_date=start_date, end_date=end_date, page_size=page_size, cursor=cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sync/status")
    async def api_sync_status(request: Request, metric_key: str | None = None) -> dict:
        _session_or_401(request)
        _validate_metrics(catalog, [metric_key] if metric_key else None)
        result = repository.sync_status(metric_key)
        result["interval_minutes"] = settings.sync_interval_minutes
        return result

    @app.post("/api/sync")
    async def api_sync(request: Request) -> JSONResponse:
        session = _session_or_401(request)
        _check_csrf(request, session)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="INVALID_JSON")
        requested = body.get("metrics") or None
        if requested is not None and (not isinstance(requested, list) or any(not isinstance(item, str) for item in requested)):
            raise HTTPException(status_code=400, detail="INVALID_METRICS")
        requested = list(dict.fromkeys(requested)) if requested else synchronizer.eligible_metrics()
        _validate_metrics(catalog, requested)
        if len(requested) > settings.max_sync_requests_per_cycle:
            raise HTTPException(status_code=400, detail="TOO_MANY_METRICS")
        mode = str(body.get("mode", "incremental"))
        if mode not in {"incremental", "backfill"}:
            raise HTTPException(status_code=400, detail="INVALID_SYNC_MODE")
        existing = repository.find_active_job(requested, mode)
        if existing:
            return JSONResponse({"job_id": existing["job_id"], "status": existing["status"], "metrics": requested}, status_code=202)
        job_id = uuid4().hex
        repository.create_job(job_id, requested, mode)
        await synchronizer.enqueue_job(job_id, requested, mode)
        return JSONResponse({"job_id": job_id, "status": "queued", "metrics": requested}, status_code=202)

    @app.get("/api/sync/jobs/{job_id}")
    async def api_sync_job(request: Request, job_id: str) -> dict:
        _session_or_401(request)
        job = repository.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
        return job

    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    async def oauth_protected_resource_metadata() -> dict:
        return {
            "resource": issuer_url,
            "authorization_servers": [issuer_url],
            "scopes_supported": ["health:read"],
            "bearer_methods_supported": ["header"],
        }

    @app.get("/mcp/consent/{request_id}", response_class=HTMLResponse, include_in_schema=False)
    async def oauth_consent(request: Request, request_id: str):
        session = session_from_request(request, sessions)
        if not session:
            return RedirectResponse(url=f"/?next={quote('/mcp/consent/' + request_id)}", status_code=303)
        pending = oauth_provider.pending_request(request_id)
        if not pending:
            return HTMLResponse("授权请求不存在或已过期", status_code=400)
        return HTMLResponse(_consent_html(pending, session.csrf))

    @app.post("/mcp/consent/{request_id}", include_in_schema=False)
    async def oauth_consent_submit(request: Request, request_id: str):
        session = session_from_request(request, sessions)
        if not session:
            return RedirectResponse(url=f"/?next={quote('/mcp/consent/' + request_id)}", status_code=303)
        body = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        csrf = body.get("csrf", [""])[0]
        if not csrf or not secrets.compare_digest(csrf, session.csrf):
            return HTMLResponse("CSRF 校验失败", status_code=403)
        try:
            if body.get("decision", ["deny"])[0] == "approve":
                target = await oauth_provider.complete_authorization(request_id, session.username)
            else:
                target = await oauth_provider.deny_authorization(request_id)
        except ValueError:
            return HTMLResponse("授权请求不存在或已过期", status_code=400)
        return RedirectResponse(url=target, status_code=303)

    app.mount("/mcp", mcp_http)
    return app


app = create_app()