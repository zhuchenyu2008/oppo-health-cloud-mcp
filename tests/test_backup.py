from pathlib import Path
import sqlite3
from oppo_health.backup import create_backup


def test_sqlite_backup_snapshot(tmp_path):
    health = tmp_path / "health.sqlite3"
    legacy = tmp_path / "cloud_cache.db"
    for path, table in ((health, "health_records"), (legacy, "cloud_data")):
        con = sqlite3.connect(path)
        con.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, value TEXT)")
        con.execute(f"INSERT INTO {table}(value) VALUES ('ok')")
        con.commit(); con.close()
    folder = create_backup(health, legacy, tmp_path / "backups")
    assert (folder / "health.sqlite3").exists()
    assert (folder / "cloud_cache.db").exists()
    assert 'health.sqlite3' in (folder / "manifest.json").read_text(encoding="utf-8")
    con = sqlite3.connect(folder / "health.sqlite3")
    assert con.execute("SELECT value FROM health_records").fetchone()[0] == "ok"
    con.close()