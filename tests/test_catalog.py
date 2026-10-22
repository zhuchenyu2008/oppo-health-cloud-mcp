import cloud_mcp

from oppo_health.catalog import GROUPS, build_catalog, category_names


def test_catalog_covers_all_mcp_choices():
    catalog = build_catalog(cloud_mcp.MCP_METRIC_CHOICES)
    assert len(catalog) == 67
    assert set(catalog) == set(cloud_mcp.MCP_METRIC_CHOICES)
    assert catalog["steps"].name == "步数记录"
    assert catalog["steps"].unit == "步"
    assert catalog["sport_file"].available is False
    assert "encryptKeyEnc" in catalog["sport_file"].caveat
    assert catalog["stress"].kind == "composite"
    assert set(GROUPS) == {"activity", "sleep", "cardiovascular", "body", "wellbeing", "cycle_environment"}
    assert category_names()["cardiovascular"] == "心血管"


def test_catalog_explains_structured_wellbeing_records():
    catalog = build_catalog(cloud_mcp.MCP_METRIC_CHOICES)
    assert catalog["physical_mental_detail"].kind == "observation"
    assert "不是一个单值" in catalog["physical_mental_detail"].description
    assert catalog["physical_mental_stat"].kind == "daily_summary"
    assert catalog["physical_mental_achievement"].kind == "snapshot"
    assert catalog["hearing"].kind == "observation"
    assert catalog["hearing_stat"].kind == "daily_summary"
    assert catalog["physical_mental_detail"].public()["kind_label"] == "测量 / 采样"
    assert catalog["physical_mental_detail"].public()["field_hints"]
