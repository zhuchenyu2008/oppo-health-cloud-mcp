import json
import sqlite3

import pytest

from oppo_health.catalog import build_catalog
from oppo_health.storage import Repository


def test_empty_legacy_db_is_safe(fake_legacy, tmp_path):
    repo = Repository(tmp_path / "health.sqlite3", fake_legacy, build_catalog(fake_legacy.MCP_METRIC_CHOICES), tmp_path / "missing.db")
    assert repo.cached_metrics() == {}
    assert repo.refresh_metric("steps") == 0
    assert repo.query_records(metric_key="steps")["records"] == []
    empty_legacy = tmp_path / "empty-existing.db"
    empty_legacy.touch()
    repo_empty = Repository(tmp_path / "health-empty.sqlite3", fake_legacy, build_catalog(fake_legacy.MCP_METRIC_CHOICES), empty_legacy)
    assert repo_empty.refresh_metric("steps") == 0


def test_raw_cache_normalizes_idempotently_and_paginates(fake_legacy, tmp_path):
    catalog = build_catalog(fake_legacy.MCP_METRIC_CHOICES)
    legacy_path = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy_path)
    con.execute("CREATE TABLE cloud_data(metric TEXT,date TEXT,payload TEXT,fetched_at INTEGER,PRIMARY KEY(metric,date))")
    for day, value in (("20261020", 200), ("20261021", 321)):
        con.execute("INSERT INTO cloud_data VALUES(?,?,?,?)", ("steps", day, json.dumps({"date": day, "steps": value}), 1))
    con.commit(); con.close()
    repo = Repository(tmp_path / "health.sqlite3", fake_legacy, catalog, legacy_path)
    assert repo.refresh_metric("steps") == 2
    assert repo.refresh_metric("steps") == 2
    first = repo.query_records(metric_key="steps", page_size=1)
    assert len(first["records"]) == 1
    assert first["has_more"] is True
    second = repo.query_records(metric_key="steps", page_size=1, cursor=first["next_cursor"])
    assert len(second["records"]) == 1
    assert second["records"][0]["local_date"] == "2026-10-20"
    assert repo.overview(7)["records_in_window"] == 2
    with pytest.raises(ValueError, match="INVALID_CATEGORY"):
        repo.query_records(category="not-a-category")
    with pytest.raises(ValueError, match="INVALID_CURSOR"):
        repo.query_records(cursor="bad")
    with pytest.raises(ValueError, match="DATE_RANGE_EXCEEDED"):
        repo.query_records(start_date="2025-01-01", end_date="2026-01-01")