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

def test_structured_records_keep_full_fields_and_chinese_kind_label(fake_legacy, tmp_path):
    catalog = build_catalog(fake_legacy.MCP_METRIC_CHOICES)
    legacy_path = tmp_path / "legacy-structured.db"
    con = sqlite3.connect(legacy_path)
    con.execute("CREATE TABLE cloud_data(metric TEXT,date TEXT,payload TEXT,fetched_at INTEGER,PRIMARY KEY(metric,date))")
    payload = {
        "date": "20261021",
        "stressValue": 42,
        "hrv": {"rmssd": 31.5, "sdnn": 48},
        "startTimeOffset": 120,
        "sourceType": "wellbeing",
    }
    con.execute("INSERT INTO cloud_data VALUES(?,?,?,?)", ("physical_mental_detail", "20261021", json.dumps(payload), 1))
    con.commit()
    con.close()

    repo = Repository(tmp_path / "health-structured.sqlite3", fake_legacy, catalog, legacy_path)
    assert repo.refresh_metric("physical_mental_detail") == 1
    record = repo.query_records(metric_key="physical_mental_detail")["records"][0]
    assert record["value"] is None
    assert record["structured"] is True
    assert record["attribute_count"] == 5
    assert record["record_kind_label"] == "测量 / 采样"
    assert record["display_name"] == "身心健康明细"
    assert record["attributes"]["hrv"] == {"rmssd": 31.5, "sdnn": 48}


def test_physical_mental_data_list_becomes_individual_chartable_rows(fake_legacy, tmp_path):
    catalog = build_catalog(fake_legacy.MCP_METRIC_CHOICES)
    legacy_path = tmp_path / "legacy-points.db"
    con = sqlite3.connect(legacy_path)
    con.execute("CREATE TABLE cloud_data(metric TEXT,date TEXT,payload TEXT,fetched_at INTEGER,PRIMARY KEY(metric,date))")
    payload = {
        "date": "20261021",
        "startTimestamp": 1792569600000,
        "endTimestamp": 1792573200000,
        "dataList": [
            {"stress": 42, "stressState": "normal", "startTimeOffset": 120},
            {"hrv": 31.5, "startTimeOffset": 240},
        ],
    }
    con.execute("INSERT INTO cloud_data VALUES(?,?,?,?)", ("physical_mental_detail", "window-1", json.dumps(payload), 1))
    con.commit()
    con.close()

    repo = Repository(tmp_path / "health-points.sqlite3", fake_legacy, catalog, legacy_path)
    assert repo.refresh_metric("physical_mental_detail") == 2
    records = repo.query_records(metric_key="physical_mental_detail")["records"]
    assert len(records) == 2
    assert records[0]["attributes"]["startTimestamp"] == payload["startTimestamp"]
    assert records[0]["recorded_at"] is not None
    assert records[0]["attributes"]["startTimeOffset"] == 120
    assert records[0]["attributes"]["stress"] == 42
    assert records[1]["attributes"]["hrv"] == 31.5
    assert "dataList" not in records[0]["attributes"]
