"""Read-only MCP surface for the normalized OPPO health cache."""
from __future__ import annotations

from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from .oauth import SQLiteOAuthProvider


def _issuer_url(settings) -> str:
    value = settings.oauth_issuer_url.rstrip("/")
    parsed = urlparse(value)
    if parsed.path in {"", "/"}:
        value += "/mcp"
    return value


def build_mcp(repository, settings):
    issuer_url = _issuer_url(settings)
    provider = SQLiteOAuthProvider(repository, issuer_url)
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(issuer_url),
        resource_server_url=AnyHttpUrl(issuer_url),
        required_scopes=["health:read"],
        validate_token_resource=True,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["health:read"],
            default_scopes=["health:read"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    mcp = FastMCP(
        name="OPPO 健康缓存",
        instructions=(
            "这是服务器端 OPPO 健康数据缓存的只读 MCP。所有工具只读取标准化缓存，"
            "不会触发云端同步、修改记录或提供医疗诊断。数据日期使用北京时间。"
        ),
        auth_server_provider=provider,
        auth=auth,
        streamable_http_path="/",
        stateless_http=True,
        host=settings.host,
        port=settings.port,
    )

    @mcp.tool(
        name="get_health_overview",
        description="读取指定周期的健康数据摘要、指标覆盖情况和同步状态；只读缓存，不触发同步。",
        annotations=read_only,
    )
    def get_health_overview(days: int = 7) -> dict:
        result = repository.overview(days=days)
        result["sync"]["interval_minutes"] = settings.sync_interval_minutes
        return result

    @mcp.tool(
        name="query_health_data",
        description="按指标、类别和北京时间日期范围分页读取标准化健康记录。复杂记录保留在 attributes 中。",
        annotations=read_only,
    )
    def query_health_data(
        category: str | None = None,
        metrics: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page_size: int = 100,
        cursor: str | None = None,
    ) -> dict:
        return repository.query_records(
            metrics=metrics,
            category=category,
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
            cursor=cursor,
        )

    @mcp.tool(
        name="list_health_metrics",
        description="读取健康指标目录，包含中文名、类别、单位、时间语义、数据类型和接入注意事项。",
        annotations=read_only,
    )
    def list_health_metrics(category: str | None = None) -> dict:
        return {"metrics": repository.catalog_rows(category=category)}

    @mcp.tool(
        name="get_sync_status",
        description="读取整体或单指标同步状态、最近错误、缓存行数和下一次计划时间；只读。",
        annotations=read_only,
    )
    def get_sync_status(metric_key: str | None = None) -> dict:
        result = repository.sync_status(metric_key=metric_key)
        result["interval_minutes"] = settings.sync_interval_minutes
        return result

    return mcp, provider, issuer_url