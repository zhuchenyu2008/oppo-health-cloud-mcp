"""Create SQLite-consistent snapshots for both raw and normalized caches."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_settings


BEIJING = ZoneInfo("Asia/Shanghai")


def _backup_one(source_path: Path, destination_path: Path) -> bool:
    if not source_path.exists():
        return False
    destination_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(destination_path, timeout=30)
    try:
        source.backup(target, pages=256, sleep=0.05)
        target.commit()
    finally:
        target.close()
        source.close()
    try:
        os.chmod(destination_path, 0o600)
    except OSError:
        pass
    return True


def create_backup(database: Path, legacy_database: Path, destination: Path) -> Path:
    now = datetime.now(BEIJING)
    folder = destination / now.strftime("%Y%m%dT%H%M%S%z")
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    backed_up = []
    if _backup_one(database, folder / "health.sqlite3"):
        backed_up.append("health.sqlite3")
    if _backup_one(legacy_database, folder / "cloud_cache.db"):
        backed_up.append("cloud_cache.db")
    if not backed_up:
        raise FileNotFoundError("No source database was found; no backup was created")
    manifest = {
        "created_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "files": backed_up,
        "consistency": "SQLite backup API snapshot per database",
    }
    manifest_path = folder / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(manifest_path, 0o600)
        os.chmod(folder, 0o700)
    except OSError:
        pass
    return folder


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="使用 SQLite backup API 备份 OPPO 健康中转数据库")
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--legacy-database", type=Path, default=settings.legacy_database_path)
    parser.add_argument("--destination", type=Path, default=Path("backups"))
    args = parser.parse_args()
    backup_path = create_backup(args.database, args.legacy_database, args.destination)
    print(backup_path)


if __name__ == "__main__":
    main()