from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import cloud_mcp
import pytest
from oppo_health.auth import hash_password
from oppo_health.config import Settings


@pytest.fixture
def fake_legacy(tmp_path: Path):
    calls: list[tuple[str, int]] = []

    def payload_date(payload):
        raw = payload.get("date") or payload.get("dataCreatedTimestamp") or payload.get("startTimestamp")
        if raw is None:
            return None
        raw = str(raw)
        if len(raw) == 8 and raw.isdigit():
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        try:
            stamp = int(raw)
            if stamp < 100_000_000_000:
                stamp *= 1000
            from datetime import datetime
            return datetime.fromtimestamp(stamp / 1000, ZoneInfo("Asia/Shanghai")).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            return raw[:10]

    def post(*args, **kwargs):
        return {"errorCode": 0, "body": {}}

    def cloud_sync(metric, days=7):
        calls.append((metric, days))
        database = Path(fake.CACHE_DB)
        database.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(database)
        con.execute("CREATE TABLE IF NOT EXISTS cloud_data(metric TEXT,date TEXT,payload TEXT,fetched_at INTEGER,PRIMARY KEY(metric,date))")
        if metric == "steps":
            payload = {"date": "20261021", "steps": 321, "source": "fixture"}
            con.execute("INSERT OR REPLACE INTO cloud_data VALUES(?,?,?,?)", (metric, "20261021", json.dumps(payload), 1792600000))
        con.commit()
        con.close()
        return {"metric": metric, "rows_cached": 1}

    def load_auth():
        return {"token": "fixture-token", "token_auth_id": "fixture-auth-id"}

    fake = SimpleNamespace(
        CACHE_DB=str(tmp_path / "legacy.db"),
        AUTH_FILE=str(tmp_path / "auth.json"),
        BEIJING=ZoneInfo("Asia/Shanghai"),
        MCP_METRIC_CHOICES=cloud_mcp.MCP_METRIC_CHOICES,
        _payload_date=payload_date,
        _load_auth=load_auth,
        _post=post,
        cloud_sync=cloud_sync,
    )
    fake.calls = calls
    return fake


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        host="127.0.0.1",
        port=8000,
        public_base_url="http://127.0.0.1:8000",
        database_path=tmp_path / "health.sqlite3",
        legacy_database_path=tmp_path / "legacy.db",
        auth_file=tmp_path / "auth.json",
        web_username="admin",
        web_password_hash=hash_password("secret"),
        oauth_issuer_url="http://127.0.0.1:8000/mcp",
        sync_interval_minutes=10,
        initial_history_days=365,
        fallback_lookback_days=7,
        retention_days=365,
        min_request_interval_ms=0,
        max_sync_requests_per_cycle=65,
        secure_cookies=False,
        session_ttl_seconds=3600,
    )