"""SQLite storage for raw OPPO payloads and normalized health records."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .catalog import COMPOSITES, KIND_LABELS, Metric, category_names

BEIJING = ZoneInfo("Asia/Shanghai")

SCHEMA = """
CREATE TABLE IF NOT EXISTS health_records (
    record_id TEXT PRIMARY KEY,
    metric_key TEXT NOT NULL,
    category TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    event_time TEXT,
    local_date TEXT,
    value_num REAL,
    unit TEXT,
    attributes_json TEXT NOT NULL,
    source_key TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    UNIQUE(metric_key, source_key)
);
CREATE INDEX IF NOT EXISTS idx_health_metric_date ON health_records(metric_key, local_date DESC, record_id DESC);
CREATE INDEX IF NOT EXISTS idx_health_category_date ON health_records(category, local_date DESC, record_id DESC);
CREATE TABLE IF NOT EXISTS sync_state (
    metric_key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued',
    mode TEXT NOT NULL DEFAULT 'incremental',
    backfill_complete INTEGER NOT NULL DEFAULT 0,
    last_started_at TEXT,
    last_success_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    rows_cached INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    next_due_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
    run_id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sync_jobs (
    job_id TEXT PRIMARY KEY,
    requested_metrics_json TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_pending (
    request_id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
    code TEXT PRIMARY KEY,
    code_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    token_hash TEXT PRIMARY KEY,
    token_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    token_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
"""

LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_data(
    metric TEXT NOT NULL,
    date TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(metric, date)
)
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_json(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}


def _payload_date(payload: dict[str, Any], legacy: Any | None = None) -> str | None:
    if legacy is not None:
        try:
            value = legacy._payload_date(payload)
            if value:
                return str(value)[:10]
        except Exception:
            pass
    for key in ("date", "dataCreatedTimestamp", "startTimestamp", "measurementTime", "timestamp", "modifiedTimestamp", "modifiedTime"):
        value = payload.get(key)
        if value is None:
            continue
        if key == "date":
            raw = str(value)
            if len(raw) == 8 and raw.isdigit():
                return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            if len(raw) >= 10:
                return raw[:10]
            continue
        try:
            stamp = int(value)
            if stamp < 100_000_000_000:
                stamp *= 1000
            return datetime.fromtimestamp(stamp / 1000, BEIJING).date().isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return None


def _numeric_value(metric_key: str, payload: dict[str, Any]) -> float | None:
    fields = {
        "heart_rate": ("heartRateValue", "heartRate", "value"),
        "heart_rate_detail": ("heartRateValue", "heartRate", "value"),
        "heart_rate_stat": ("heartRateValue", "avgHeartRate", "averageHeartRate"),
        "blood_oxygen": ("bloodOxygenSaturationValue", "bloodOxygen", "value"),
        "blood_oxygen_detail": ("bloodOxygenSaturationValue", "bloodOxygen", "value"),
        "blood_oxygen_stat": ("bloodOxygenSaturationValue", "average", "avgValue"),
        "breath_rate": ("respiratoryRate", "breathRate", "value"),
        "steps": ("steps", "step", "value"),
        "steps_detail": ("steps", "step", "value"),
        "steps_stat": ("totalSteps", "steps", "value"),
        "weight": ("weight", "weightValue", "value"),
        "wrist_temperature": ("wristTemperatureValue", "temperatureValue", "value"),
        "wrist_temperature_detail": ("wristTemperatureValue", "temperatureValue", "value"),
        "hrv": ("heartRateVarValue", "hrv", "value"),
        "stress_detail": ("stressValue", "stress", "value"),
        "stress_stat": ("stressValue", "stress", "value"),
        "sleep_index": ("sleepIndex", "index", "value"),
        "exercise_intensity": ("intensity", "exerciseIntensity", "value"),
    }.get(metric_key, ())
    for field in fields:
        value = payload.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # OPPO's weight endpoint commonly returns grams; preserve the legacy conversion.
            return float(value) / 1000.0 if metric_key == "weight" and abs(float(value)) > 300 else float(value)
    return None


def _record_time(payload: dict[str, Any], legacy: Any | None = None) -> tuple[str | None, str | None]:
    for key in ("date", "dataCreatedTimestamp", "startTimestamp", "measurementTime", "timestamp", "modifiedTimestamp", "modifiedTime"):
        value = payload.get(key)
        if value is None:
            continue
        if key == "date":
            raw = str(value)
            normalized = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else raw[:10]
            try:
                day = date.fromisoformat(normalized)
                # Date-only records carry a calendar day, not a precise instant.
                return None, day.isoformat()
            except ValueError:
                continue
        try:
            stamp = int(value)
            if stamp <= 0:
                continue
            if stamp < 100_000_000_000:
                stamp *= 1000
            dt = datetime.fromtimestamp(stamp / 1000, timezone.utc)
            local_day = datetime.fromtimestamp(stamp / 1000, BEIJING).date().isoformat()
            return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z"), local_day
        except (ValueError, TypeError, OSError, OverflowError):
            continue
    return None, _payload_date(payload, legacy)


def _flatten(metric_key: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    # These endpoints wrap actual samples in dataList. Split known point-series into
    # individual rows while carrying their parent interval/context forward. Never
    # coerce a multi-field point into one arbitrarily chosen scalar.
    if metric_key in {"breath_rate", "physical_mental_detail"} and isinstance(payload.get("dataList"), list):
        # Keep the parent context, but let a point timestamp win over the parent
        # calendar date so chart points retain their actual time within the interval.
        parent = {key: value for key, value in payload.items() if key not in {"dataList", "date"}}
        rows: list[dict[str, Any]] = []
        for point in payload["dataList"]:
            if not isinstance(point, dict):
                continue
            row = {**parent, **point}
            row["startTimestamp"] = point.get("startTimestamp", payload.get("startTimestamp"))
            row["endTimestamp"] = point.get("endTimestamp", payload.get("endTimestamp"))
            if not any(row.get(key) is not None for key in ("dataCreatedTimestamp", "startTimestamp", "measurementTime", "timestamp")):
                row.setdefault("date", payload.get("date"))
            row.setdefault("sourceType", f"{metric_key}.dataList")
            rows.append(row)
        return rows or [payload]
    return [payload]
def _normalize(metric: Metric, source_key: str, payload: dict[str, Any], synced_at: str, legacy: Any | None) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    flattened = _flatten(metric.key, payload)
    for item_index, item in enumerate(flattened):
        event_time, local_day = _record_time(item, legacy)
        if local_day is None:
            local_day = _record_time(payload, legacy)[1]
        item_id = item.get("id") or item.get("recordId") or item.get("dataId")
        device_id = item.get("deviceId") or item.get("deviceSn") or item.get("deviceNo")
        if item_id is not None:
            source_identity = f"{device_id}:{item_id}" if device_id is not None else str(item_id)
        else:
            source_identity = source_key
        if len(flattened) > 1:
            source_identity += f":{item_index}"
        record_id = _sha(f"{metric.key}\0{source_identity}")
        attrs = {key: value for key, value in item.items() if key != "__cacheKey"}
        rows.append((
            record_id,
            metric.key,
            metric.category,
            metric.kind,
            event_time,
            local_day,
            _numeric_value(metric.key, item),
            metric.unit,
            json.dumps(attrs, ensure_ascii=False, separators=(",", ":"), default=str),
            source_identity,
            synced_at,
        ))
    return rows


class Repository:
    """Normalized database plus a read-through view of the legacy raw cache."""

    def __init__(self, database_path: str | Path, legacy: Any, catalog: dict[str, Metric], legacy_database_path: str | Path | None = None):
        self.path = Path(database_path)
        self.legacy = legacy
        self.catalog = catalog
        self.legacy_path = Path(legacy_database_path or getattr(legacy, "CACHE_DB", "data/cloud_cache.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    def _legacy_connect(self, *, create: bool = False) -> sqlite3.Connection | None:
        if not self.legacy_path.exists() and not create:
            return None
        con = sqlite3.connect(self.legacy_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        if create:
            con.execute(LEGACY_SCHEMA)
            con.commit()
        return con

    def initialize(self) -> None:
        con = self.connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            columns = {row["name"] for row in con.execute("PRAGMA table_info(sync_state)")}
            if "failure_count" not in columns:
                con.execute("ALTER TABLE sync_state ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
            con.commit()
        finally:
            con.close()
        self.ensure_sync_states()

    def ensure_sync_states(self) -> None:
        now = utc_now()
        keys = [key for key in self.catalog if key not in COMPOSITES]
        con = self.connect()
        try:
            con.executemany("INSERT OR IGNORE INTO sync_state(metric_key,updated_at) VALUES(?,?)", [(key, now) for key in keys])
            con.commit()
        finally:
            con.close()

    def cached_metrics(self) -> dict[str, int]:
        con = self._legacy_connect()
        if con is None:
            return {}
        try:
            con.execute(LEGACY_SCHEMA)
            return {row["metric"]: int(row["n"]) for row in con.execute("SELECT metric, COUNT(*) AS n FROM cloud_data GROUP BY metric")}
        finally:
            con.close()

    def refresh_metric(self, metric_key: str) -> int:
        if metric_key not in self.catalog or metric_key in COMPOSITES:
            return 0
        legacy_con = self._legacy_connect()
        if legacy_con is None:
            return 0
        con = self.connect()
        synced = utc_now()
        try:
            legacy_con.execute(LEGACY_SCHEMA)
            raw = legacy_con.execute("SELECT date, payload FROM cloud_data WHERE metric=? ORDER BY date", (metric_key,)).fetchall()
            normalized_rows: list[tuple[Any, ...]] = []
            metric = self.catalog[metric_key]
            for row in raw:
                payload = _safe_json(row["payload"])
                if not isinstance(payload, dict):
                    continue
                normalized_rows.extend(_normalize(metric, str(row["date"]), payload, synced, self.legacy))
            if normalized_rows:
                con.executemany(
                    """INSERT INTO health_records
                    (record_id,metric_key,category,record_kind,event_time,local_date,value_num,unit,attributes_json,source_key,synced_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(metric_key,source_key) DO UPDATE SET
                    record_id=excluded.record_id,category=excluded.category,record_kind=excluded.record_kind,
                    event_time=excluded.event_time,local_date=excluded.local_date,value_num=excluded.value_num,
                    unit=excluded.unit,attributes_json=excluded.attributes_json,synced_at=excluded.synced_at""",
                    normalized_rows,
                )
            con.commit()
            return len(normalized_rows)
        finally:
            legacy_con.close()
            con.close()

    @staticmethod
    def _encode_cursor(local_date: str, record_id: str) -> str:
        raw = json.dumps({"date": local_date, "id": record_id}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode())
            return str(value["date"]), str(value["id"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise ValueError("INVALID_CURSOR") from None

    def query_records(
        self,
        metric_key: str | None = None,
        *,
        metrics: Iterable[str] | None = None,
        category: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page_size: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= page_size <= 500:
            raise ValueError("INVALID_PAGE_SIZE")
        keys: list[str] = []
        if metric_key:
            keys.extend(COMPOSITES.get(metric_key, (metric_key,)))
        if metrics:
            for key in metrics:
                keys.extend(COMPOSITES.get(key, (key,)))
        if keys:
            keys = list(dict.fromkeys(keys))
            unknown = [key for key in keys if key not in self.catalog]
            if unknown:
                raise ValueError(f"METRIC_NOT_FOUND:{unknown[0]}")
        for value in (start_date, end_date):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise ValueError("INVALID_DATE") from None
        if start_date and end_date and start_date > end_date:
            raise ValueError("INVALID_DATE_RANGE")
        if start_date and end_date:
            if (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days >= 365:
                raise ValueError("DATE_RANGE_EXCEEDED")
        decoded = self._decode_cursor(cursor)
        filters: list[str] = []
        params: list[Any] = []
        if keys:
            filters.append(f"metric_key IN ({','.join('?' for _ in keys)})")
            params.extend(keys)
        if category:
            if category not in category_names():
                raise ValueError("INVALID_CATEGORY")
            filters.append("category=?")
            params.append(category)
        if start_date:
            filters.append("local_date>=?")
            params.append(start_date)
        if end_date:
            filters.append("local_date<=?")
            params.append(end_date)
        if decoded:
            filters.append("(COALESCE(local_date,'')<? OR (COALESCE(local_date,'')=? AND record_id<?))")
            params.extend([decoded[0], decoded[0], decoded[1]])
        where = " WHERE " + " AND ".join(filters) if filters else ""
        con = self.connect()
        try:
            rows = con.execute(
                f"SELECT * FROM health_records{where} ORDER BY COALESCE(local_date,'') DESC, record_id DESC LIMIT ?",
                (*params, page_size + 1),
            ).fetchall()
            has_more = len(rows) > page_size
            visible = rows[:page_size]
            return {
                "records": [self._public_row(row) for row in visible],
                "has_more": has_more,
                "next_cursor": self._encode_cursor(visible[-1]["local_date"] or "", visible[-1]["record_id"]) if has_more and visible else None,
            }
        finally:
            con.close()

    def _public_row(self, row: sqlite3.Row) -> dict[str, Any]:
        metric = self.catalog.get(row["metric_key"])
        # Use catalog metadata for the label as well as the persisted kind. This keeps
        # records created before a catalog refinement understandable in the UI.
        display_kind = metric.kind if metric else row["record_kind"]
        attributes = _safe_json(row["attributes_json"])
        return {
            "record_id": row["record_id"],
            "metric_key": row["metric_key"],
            "display_name": metric.name if metric else row["metric_key"],
            "category": row["category"],
            "record_kind": row["record_kind"],
            "record_kind_label": KIND_LABELS.get(display_kind, display_kind),
            "recorded_at": row["event_time"],
            "local_date": row["local_date"],
            "value": row["value_num"],
            "unit": row["unit"],
            "attributes": attributes,
            "attribute_count": len(attributes) if isinstance(attributes, dict) else 0,
            "structured": row["value_num"] is None,
            "source": {"dataset": row["metric_key"], "source_type": "oppo_cloud", "source_key": row["source_key"]},
            "synced_at": row["synced_at"],
        }
    def overview(self, days: int = 7) -> dict[str, Any]:
        if not 1 <= days <= 365:
            raise ValueError("INVALID_ARGUMENT")
        today = datetime.now(BEIJING).date()
        start = today - timedelta(days=days - 1)
        start_text, end_text = start.isoformat(), today.isoformat()
        con = self.connect()
        summaries: list[dict[str, Any]] = []
        coverage: dict[str, dict[str, Any]] = {}
        try:
            rows = con.execute(
                """SELECT metric_key, COUNT(*) AS record_count, COUNT(DISTINCT local_date) AS days_covered,
                   MAX(local_date) AS latest_date
                   FROM health_records WHERE local_date>=? AND local_date<=? GROUP BY metric_key""",
                (start_text, end_text),
            ).fetchall()
            for row in rows:
                metric = self.catalog.get(row["metric_key"])
                if metric:
                    coverage[row["metric_key"]] = {
                        "metric_key": row["metric_key"],
                        "name": metric.name,
                        "category": metric.category,
                        "record_count": int(row["record_count"]),
                        "days_covered": int(row["days_covered"]),
                        "latest_date": row["latest_date"],
                    }
                    latest = con.execute(
                        """SELECT * FROM health_records
                           WHERE metric_key=? AND local_date>=? AND local_date<=?
                           ORDER BY COALESCE(event_time,'') DESC, record_id DESC LIMIT 1""",
                        (row["metric_key"], start_text, end_text),
                    ).fetchone()
                    if latest:
                        public = self._public_row(latest)
                        summaries.append({
                            "metric_key": metric.key,
                            "name": metric.name,
                            "category": metric.category,
                            "value": public["value"],
                            "unit": public["unit"],
                            "local_date": public["local_date"],
                            "record_kind": public["record_kind"],
                            "attributes": public["attributes"],
                            "record_count": int(row["record_count"]),
                            "days_covered": int(row["days_covered"]),
                            "synced_at": public["synced_at"],
                        })
            total = int(con.execute("SELECT COUNT(*) FROM health_records WHERE local_date>=? AND local_date<=?", (start_text, end_text)).fetchone()[0])
        finally:
            con.close()
        return {
            "period": {"days": days, "start_date": start_text, "end_date": end_text, "timezone": "Asia/Shanghai"},
            "summaries": sorted(summaries, key=lambda item: (item["category"], item["metric_key"])),
            "coverage": coverage,
            "records_in_window": total,
            "sync": self.sync_status(),
        }

    def catalog_rows(self, category: str | None = None) -> list[dict[str, Any]]:
        if category and category not in category_names():
            raise ValueError("INVALID_CATEGORY")
        return [
            metric.public() | {"category_name": category_names().get(metric.category, "其他"), "count": self.metric_count(metric.key)}
            for metric in self.catalog.values()
            if category is None or metric.category == category
        ]

    def metric_count(self, key: str) -> int:
        keys = COMPOSITES.get(key, (key,))
        con = self.connect()
        try:
            return sum(int(con.execute("SELECT COUNT(*) FROM health_records WHERE metric_key=?", (source,)).fetchone()[0]) for source in keys)
        finally:
            con.close()

    def set_sync_state(self, metric_key: str, **values: Any) -> None:
        allowed = {"status", "mode", "backfill_complete", "last_started_at", "last_success_at", "last_error_code", "last_error_message", "rows_cached", "failure_count", "next_due_at"}
        update = {key: value for key, value in values.items() if key in allowed}
        update["updated_at"] = utc_now()
        con = self.connect()
        try:
            con.execute("INSERT OR IGNORE INTO sync_state(metric_key,updated_at) VALUES(?,?)", (metric_key, update["updated_at"]))
            sets = ",".join(f"{key}=?" for key in update)
            con.execute(f"UPDATE sync_state SET {sets} WHERE metric_key=?", (*update.values(), metric_key))
            con.commit()
        finally:
            con.close()

    def sync_status(self, metric_key: str | None = None) -> dict[str, Any]:
        self.ensure_sync_states()
        requested = COMPOSITES.get(metric_key, (metric_key,)) if metric_key else None
        con = self.connect()
        try:
            if requested:
                rows = con.execute(f"SELECT * FROM sync_state WHERE metric_key IN ({','.join('?' for _ in requested)}) ORDER BY metric_key", tuple(requested)).fetchall()
            else:
                rows = con.execute("SELECT * FROM sync_state ORDER BY metric_key").fetchall()
            metrics = []
            for row in rows:
                metric = self.catalog.get(row["metric_key"])
                metrics.append(dict(row) | {
                    "name": metric.name if metric else row["metric_key"],
                    "category": metric.category if metric else "other",
                    "available": metric.available if metric else True,
                })
            statuses = {row["status"] for row in rows}
            if "running" in statuses or "backfilling" in statuses:
                overall = "running"
            elif "failed" in statuses and not any(status == "success" for status in statuses):
                overall = "failed"
            elif statuses == {"blocked_auth"}:
                overall = "blocked_auth"
            elif {"failed", "blocked_capability", "blocked_auth"} & statuses:
                overall = "partial"
            elif "success" in statuses:
                overall = "success"
            else:
                overall = "queued"
            return {"overall_status": overall, "metrics": metrics}
        finally:
            con.close()

    def create_job(self, job_id: str, metrics: list[str], mode: str) -> None:
        con = self.connect()
        try:
            con.execute(
                "INSERT INTO sync_jobs(job_id,requested_metrics_json,mode,status,created_at) VALUES(?,?,?,?,?)",
                (job_id, json.dumps(metrics, ensure_ascii=False), mode, "queued", utc_now()),
            )
            con.commit()
        finally:
            con.close()

    def find_active_job(self, metrics: list[str], mode: str) -> dict[str, Any] | None:
        con = self.connect()
        try:
            rows = con.execute(
                "SELECT * FROM sync_jobs WHERE status IN ('queued','running') ORDER BY created_at DESC"
            ).fetchall()
            requested = sorted(set(metrics))
            for row in rows:
                if row["mode"] == mode and sorted(set(_safe_json(row["requested_metrics_json"]))) == requested:
                    data = dict(row)
                    data["requested_metrics"] = _safe_json(data.pop("requested_metrics_json"))
                    data["result"] = _safe_json(data.pop("result_json")) if data.get("result_json") else None
                    return data
            return None
        finally:
            con.close()

    def update_job(self, job_id: str, status: str, *, result: dict[str, Any] | None = None, started: bool = False) -> None:
        con = self.connect()
        try:
            con.execute(
                "UPDATE sync_jobs SET status=?,started_at=COALESCE(started_at,?),ended_at=?,result_json=? WHERE job_id=?",
                (
                    status,
                    utc_now() if started else None,
                    utc_now() if status in {"success", "partial", "failed"} else None,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    job_id,
                ),
            )
            con.commit()
        finally:
            con.close()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        con = self.connect()
        try:
            row = con.execute("SELECT * FROM sync_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                return None
            data = dict(row)
            data["requested_metrics"] = _safe_json(data.pop("requested_metrics_json"))
            data["result"] = _safe_json(data.pop("result_json")) if data.get("result_json") else None
            return data
        finally:
            con.close()

    def prune(self, retention_days: int) -> int:
        if retention_days < 1:
            return 0
        today = datetime.now(BEIJING).date()
        cutoff = today - timedelta(days=retention_days - 1)
        removed = 0
        legacy_con = self._legacy_connect()
        if legacy_con is not None:
            try:
                legacy_con.execute(LEGACY_SCHEMA)
                rows = legacy_con.execute("SELECT metric,date,payload,fetched_at FROM cloud_data").fetchall()
                raw_keys = []
                for row in rows:
                    payload = _safe_json(row["payload"])
                    day = _payload_date(payload, self.legacy) if isinstance(payload, dict) else None
                    if day is None:
                        day = _payload_date({"date": row["date"]}, self.legacy)
                    if day is None:
                        try:
                            day = datetime.fromtimestamp(int(row["fetched_at"]), BEIJING).date().isoformat()
                        except (TypeError, ValueError, OSError, OverflowError):
                            day = None
                    if day and day < cutoff.isoformat():
                        raw_keys.append((row["metric"], row["date"]))
                if raw_keys:
                    legacy_con.executemany("DELETE FROM cloud_data WHERE metric=? AND date=?", raw_keys)
                    removed += len(raw_keys)
                legacy_con.commit()
            finally:
                legacy_con.close()
        cutoff_utc = datetime.combine(cutoff, datetime.min.time(), BEIJING).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        con = self.connect()
        try:
            con.execute(
                "DELETE FROM health_records WHERE (local_date IS NOT NULL AND local_date<?) OR (local_date IS NULL AND synced_at<?)",
                (cutoff.isoformat(), cutoff_utc),
            )
            removed += con.execute("SELECT changes()").fetchone()[0]
            con.commit()
        finally:
            con.close()
        return removed

    def raw_to_health(self, metric_key: str) -> int:
        return self.refresh_metric(metric_key)
