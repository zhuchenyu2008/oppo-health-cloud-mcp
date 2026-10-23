import asyncio
import json

from oppo_health.app import create_app


def test_mcp_has_only_read_only_tools(settings, fake_legacy):
    app = create_app(settings, fake_legacy)
    tools = asyncio.run(app.state.mcp.list_tools())
    assert {tool.name for tool in tools} == {
        "get_health_overview", "query_health_data", "list_health_metrics", "get_sync_status"
    }
    assert all(tool.annotations.readOnlyHint is True for tool in tools)
    query_tool = next(tool for tool in tools if tool.name == "query_health_data")
    assert "metrics" in query_tool.inputSchema["properties"]
    assert "metric_key" not in query_tool.inputSchema["properties"]
    result = asyncio.run(app.state.mcp.call_tool("list_health_metrics", {"category": "body"}))
    assert result
    wellbeing = asyncio.run(app.state.mcp.call_tool("list_health_metrics", {"category": "wellbeing"}))
    wellbeing_payload = json.loads(wellbeing[0].text)
    physical = next(item for item in wellbeing_payload["metrics"] if item["key"] == "physical_mental_detail")
    assert physical["value_context"]["value_direction"] == "higher_is_better"
    assert "个性化测量模型" not in str(physical["value_context"])
    query_result = asyncio.run(app.state.mcp.call_tool("query_health_data", {
        "metrics": ["steps", "heart_rate"],
        "start_date": "2026-10-01",
        "end_date": "2026-10-22",
    }))
    assert query_result
    assert fake_legacy.calls == []
