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