"""Application configuration. Secret values are only read from mounted files/env."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_env: str
    host: str
    port: int
    public_base_url: str
    database_path: Path
    legacy_database_path: Path
    auth_file: Path
    web_username: str
    web_password_hash: str
    oauth_issuer_url: str
    sync_interval_minutes: int
    initial_history_days: int
    fallback_lookback_days: int
    retention_days: int
    min_request_interval_ms: int
    max_sync_requests_per_cycle: int
    secure_cookies: bool
    session_ttl_seconds: int


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.getenv("OPPO_HEALTH_CONFIG", "config.toml"))
    config = _read_toml(config_path)
    server = config.get("server", {})
    storage = config.get("storage", {})
    sync = config.get("sync", {})
    security = config.get("security", {})
    app_env = os.getenv("APP_ENV", "production")
    public_url = os.getenv("PUBLIC_BASE_URL", server.get("public_base_url", "")).rstrip("/")
    default_issuer = (public_url + "/mcp") if public_url else "http://127.0.0.1:8000/mcp"
    return Settings(
        app_env=app_env,
        host=os.getenv("HOST", str(server.get("host", "0.0.0.0"))),
        port=int(os.getenv("PORT", server.get("port", 8000))),
        public_base_url=public_url,
        database_path=Path(os.getenv("DATABASE_PATH", storage.get("database_path", "data/health.sqlite3"))).resolve(),
        legacy_database_path=Path(os.getenv("LEGACY_DATABASE_PATH", storage.get("legacy_database_path", "data/cloud_cache.db"))).resolve(),
        auth_file=Path(os.getenv("OPPO_AUTH_FILE", storage.get("auth_file", "auth.json"))).resolve(),
        web_username=os.getenv("WEB_USERNAME", str(security.get("username", "admin"))),
        web_password_hash=os.getenv("WEB_PASSWORD_HASH", str(security.get("password_hash", ""))),
        oauth_issuer_url=(os.getenv("OAUTH_ISSUER_URL", security.get("oauth_issuer_url", default_issuer)) or default_issuer).rstrip("/"),
        sync_interval_minutes=max(1, int(os.getenv("SYNC_INTERVAL_MINUTES", sync.get("interval_minutes", 10)))),
        initial_history_days=max(1, int(os.getenv("INITIAL_HISTORY_DAYS", sync.get("initial_history_days", 365)))),
        fallback_lookback_days=max(1, int(os.getenv("FALLBACK_LOOKBACK_DAYS", sync.get("fallback_lookback_days", 7)))),
        retention_days=max(1, int(os.getenv("RETENTION_DAYS", storage.get("retention_days", 365)))),
        min_request_interval_ms=max(0, int(os.getenv("MIN_REQUEST_INTERVAL_MS", sync.get("min_request_interval_ms", 1000)))),
        max_sync_requests_per_cycle=max(1, int(os.getenv("MAX_SYNC_REQUESTS_PER_CYCLE", sync.get("max_metrics_per_cycle", 65)))),
        secure_cookies=os.getenv("SECURE_COOKIES", "true" if app_env == "production" else "false").lower() in {"1", "true", "yes"},
        session_ttl_seconds=max(300, int(os.getenv("SESSION_TTL_SECONDS", security.get("session_ttl_seconds", 12 * 3600)))),
    )
