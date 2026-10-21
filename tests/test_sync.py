import asyncio

from oppo_health.catalog import build_catalog
from oppo_health.storage import Repository
from oppo_health.sync import SyncService


def test_sync_backfill_deduplicates_and_respects_due(settings, fake_legacy, tmp_path):
    catalog = build_catalog(fake_legacy.MCP_METRIC_CHOICES)
    settings = settings.__class__(**(settings.__dict__ | {"database_path": tmp_path / "health.sqlite3", "legacy_database_path": tmp_path / "legacy.db"}))
    repo = Repository(settings.database_path, fake_legacy, catalog, settings.legacy_database_path)
    service = SyncService(repo, fake_legacy, settings, catalog)
    first = asyncio.run(service.sync(["steps"], mode="backfill"))
    assert first["status"] == "success"
    assert fake_legacy.calls == [("steps", 365)]
    assert repo.metric_count("steps") == 1
    second = asyncio.run(service.sync(["steps"], mode="backfill"))
    assert second["metrics"][0]["status"] == "deferred"
    third = asyncio.run(service.sync(["steps"], mode="incremental", force=True))
    assert third["status"] == "success"
    assert fake_legacy.calls[-1] == ("steps", 7)
    assert repo.metric_count("steps") == 1
    service.close()

def test_missing_oppo_auth_blocks_without_upstream_calls(settings, fake_legacy, tmp_path):
    catalog = build_catalog(fake_legacy.MCP_METRIC_CHOICES)
    settings = settings.__class__(**(settings.__dict__ | {"database_path": tmp_path / "health.sqlite3", "legacy_database_path": tmp_path / "legacy.db"}))
    repo = Repository(settings.database_path, fake_legacy, catalog, settings.legacy_database_path)

    def missing_auth():
        raise FileNotFoundError("auth not mounted")

    fake_legacy._load_auth = missing_auth
    service = SyncService(repo, fake_legacy, settings, catalog)
    result = asyncio.run(service.sync(["steps"], mode="backfill", force=True))
    assert result["metrics"][0]["status"] == "blocked_auth"
    assert repo.sync_status("steps")["metrics"][0]["status"] == "blocked_auth"
    assert fake_legacy.calls == []
    service.close()


def test_upstream_retries_transient_curl_errors(monkeypatch, settings, fake_legacy, tmp_path):
    import oppo_health.sync as sync_module

    catalog = build_catalog(fake_legacy.MCP_METRIC_CHOICES)
    settings = settings.__class__(**(settings.__dict__ | {"database_path": tmp_path / "health.sqlite3", "legacy_database_path": tmp_path / "legacy.db"}))
    repo = Repository(settings.database_path, fake_legacy, catalog, settings.legacy_database_path)
    service = SyncService(repo, fake_legacy, settings, catalog)
    responses = iter([{"http_error": 28, "body": "non_json_response"}, {"errorCode": 0, "body": {"ok": True}}])
    requests = []
    sleeps = []

    def original(path, body, auth, timeout=20):
        requests.append((path, body))
        return next(responses)

    monkeypatch.setattr(service, "_original_post", original)
    monkeypatch.setattr(sync_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    assert fake_legacy._post("/fixture", {"page": 1}, {}) == {"errorCode": 0, "body": {"ok": True}}
    assert len(requests) == 2
    assert sleeps == [2]
    service.close()
