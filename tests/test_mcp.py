import asyncio

from oppo_health.app import create_app


def test_mcp_has_only_read_only_tools(settings, fake_legacy):
    app = create_app(settings, fake_legacy)
    tools = asyncio.run(app.state.mcp.list_tools())
    assert {tool.name for tool in tools} == {
        "get_health_overview", "query_health_data", "list_health_metrics", "get_sync_status"
    }
    assert all(tool.annotations.readOnlyHint is True for tool in tools)
    result = asyncio.run(app.state.mcp.call_tool("list_health_metrics", {"category": "body"}))
    assert result
    assert fake_legacy.calls == []