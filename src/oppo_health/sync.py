"""Periodic and user-triggered synchronization orchestration."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .catalog import COMPOSITES
from .storage import utc_now

log = logging.getLogger(__name__)


class RequestPacer:
    """Process-wide minimum spacing between upstream requests."""

    def __init__(self, interval_ms: int):
        self.interval = max(0, interval_ms) / 1000
        self._lock = threading.Lock()
        self._next_at = 0.0

    def call(self, fn, *args, **kwargs):
        with self._lock:
            wait = self._next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_at = time.monotonic() + self.interval
        return fn(*args, **kwargs)


class SyncService:
    def __init__(self, repository, legacy, settings, catalog):
        self.repo = repository
        self.legacy = legacy
        self.settings = settings
        self.catalog = catalog
        self._run_lock = asyncio.Lock()
        self._job_queue: asyncio.Queue[tuple[str, list[str], str]] = asyncio.Queue()
        self._job_worker: asyncio.Task | None = None
        self._pacer = RequestPacer(settings.min_request_interval_ms)
        self._original_post = getattr(legacy, "_oppo_relay_original_post", legacy._post)
        setattr(legacy, "_oppo_relay_original_post", self._original_post)
        self._original_cache_db = legacy.CACHE_DB
        self._original_auth_file = legacy.AUTH_FILE
        legacy.CACHE_DB = str(settings.legacy_database_path)
        legacy.AUTH_FILE = str(settings.auth_file)

        def paced_post(*args, **kwargs):
            retry_delays = (2, 5, 15)
            for attempt in range(len(retry_delays) + 1):
                result = self._pacer.call(self._original_post, *args, **kwargs)
                if not isinstance(result, dict):
                    return result
                body = result.get("body")
                code = result.get("errorCode")
                if code is None and isinstance(body, dict):
                    code = body.get("errorCode")
                try:
                    code_number = int(code) if code is not None else None
                except (TypeError, ValueError):
                    code_number = None
                curl_error = result.get("http_error")
                transient = (
                    (curl_error is not None and int(curl_error) != 0)
                    or body == "non_json_response"
                    or code_number == 429
                    or (code_number is not None and 500 <= code_number <= 599)
                )
                # These stable application errors indicate request/auth/range
                # mistakes; repeating them immediately cannot help.
                if code_number in {10100, 10103, 23103}:
                    transient = False
                if not transient or attempt >= len(retry_delays):
                    return result
                delay = retry_delays[attempt]
                log.warning("transient OPPO request failure; retry=%s delay_seconds=%s", attempt + 1, delay)
                time.sleep(delay)
            return result

        # Keep the proven curl/TLS implementation from cloud_mcp.py while adding
        # global pacing and bounded retries around each signed request.
        legacy._post = paced_post
        self.repo.initialize()
        self._auth_ready = self._check_auth_file()
        for key, metric in self.catalog.items():
            if key not in COMPOSITES and not metric.available:
                self.repo.set_sync_state(
                    key,
                    status="blocked_capability",
                    last_error_code="capability_missing",
                    last_error_message=metric.caveat,
                )
            elif key not in COMPOSITES and not self._auth_ready:
                self.repo.set_sync_state(
                    key,
                    status="blocked_auth",
                    last_error_code="OPPO_AUTH_UNAVAILABLE",
                    last_error_message="OPPO 认证文件缺失或格式无效",
                )

    def _check_auth_file(self) -> bool:
        try:
            auth = self.legacy._load_auth()
            return bool(auth.get("token") and auth.get("token_auth_id"))
        except (OSError, ValueError, TypeError, AssertionError, AttributeError):
            return False

    async def shutdown(self) -> None:
        if self._job_worker and not self._job_worker.done():
            self._job_worker.cancel()
            try:
                await self._job_worker
            except asyncio.CancelledError:
                pass
            self._job_worker = None

    def close(self) -> None:
        self.legacy._post = self._original_post
        self.legacy.CACHE_DB = self._original_cache_db
        self.legacy.AUTH_FILE = self._original_auth_file
        if getattr(self.legacy, "_oppo_relay_original_post", None) is self._original_post:
            delattr(self.legacy, "_oppo_relay_original_post")

    def eligible_metrics(self) -> list[str]:
        return [key for key, metric in self.catalog.items() if key not in COMPOSITES and metric.available]

    def _next_due(self, failure_count: int = 0) -> str:
        # Successful metrics follow the configured cadence. Failed metrics use
        # exponential backoff from one minute to a one-hour maximum.
        delay = self.settings.sync_interval_minutes if failure_count <= 0 else min(60, self.settings.sync_interval_minutes * (2 ** min(failure_count - 1, 6)))
        return (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _sync_one(self, metric_key: str, mode: str, *, force: bool = False) -> dict:
        started = utc_now()
        state = next((row for row in self.repo.sync_status(metric_key)["metrics"]), {})
        if not self._check_auth_file():
            self.repo.set_sync_state(
                metric_key,
                status="blocked_auth",
                last_error_code="OPPO_AUTH_UNAVAILABLE",
                last_error_message="OPPO 认证文件缺失或格式无效",
            )
            return {"metric_key": metric_key, "status": "blocked_auth", "error_code": "OPPO_AUTH_UNAVAILABLE"}
        self._auth_ready = True
        due_at = state.get("next_due_at")
        if not force and due_at and due_at > started and state.get("status") in {"success", "failed"}:
            return {"metric_key": metric_key, "status": "deferred", "next_due_at": due_at}
        first = not bool(state.get("backfill_complete"))
        do_backfill = mode == "backfill" and first
        days = self.settings.initial_history_days if do_backfill else self.settings.fallback_lookback_days
        actual_mode = "backfill" if do_backfill else "incremental"
        self.repo.set_sync_state(
            metric_key,
            status="backfilling" if actual_mode == "backfill" else "running",
            mode=actual_mode,
            last_started_at=started,
            next_due_at=None,
            last_error_code=None,
            last_error_message=None,
        )
        try:
            result = self.legacy.cloud_sync(metric_key, days=days)
            if not isinstance(result, dict):
                raise RuntimeError("invalid_sync_response")
            if result.get("error") or result.get("errors") or result.get("errorCode") not in (None, 0, "0"):
                errors = result.get("errors") or [result]
                first_error = errors[0] if isinstance(errors[0], dict) else {"error": str(errors[0])}
                code = str(first_error.get("errorCode", first_error.get("error", "upstream_error")))
                message = str(first_error.get("error", first_error.get("message", "upstream_error")))[:200]
                failure_count = int(state.get("failure_count", 0)) + 1
                self.repo.set_sync_state(
                    metric_key,
                    status="failed",
                    failure_count=failure_count,
                    last_error_code=code,
                    last_error_message=message,
                    next_due_at=self._next_due(failure_count),
                )
                return {"metric_key": metric_key, "status": "failed", "error_code": code, "message": message}
            rows = self.repo.refresh_metric(metric_key)
            self.repo.set_sync_state(
                metric_key,
                status="success",
                mode="incremental",
                backfill_complete=1 if actual_mode == "backfill" or bool(state.get("backfill_complete")) else 0,
                last_success_at=utc_now(),
                last_error_code=None,
                last_error_message=None,
                rows_cached=rows,
                failure_count=0,
                next_due_at=self._next_due(),
            )
            return {"metric_key": metric_key, "status": "success", "rows_cached": rows, "requested_days": days}
        except Exception as exc:
            log.exception("sync metric failed: %s", metric_key)
            failure_count = int(state.get("failure_count", 0)) + 1
            self.repo.set_sync_state(
                metric_key,
                status="failed",
                failure_count=failure_count,
                last_error_code=type(exc).__name__,
                last_error_message=str(exc)[:200],
                next_due_at=self._next_due(failure_count),
            )
            return {"metric_key": metric_key, "status": "failed", "error_code": type(exc).__name__, "message": str(exc)[:200]}

    def _sync_sequence(self, metric_keys: list[str], mode: str, force: bool) -> dict:
        run_id = uuid4().hex
        started = utc_now()
        con = self.repo.connect()
        try:
            con.execute(
                "INSERT INTO sync_runs(run_id,trigger,started_at,status,metrics_json) VALUES(?,?,?,?,?)",
                (run_id, mode, started, "running", "{}"),
            )
            con.commit()
        finally:
            con.close()

        results: list[dict] = []
        for key in metric_keys:
            if key not in self.catalog:
                results.append({"metric_key": key, "status": "skipped", "error_code": "METRIC_NOT_FOUND"})
                continue
            if key in COMPOSITES:
                for child in COMPOSITES[key]:
                    results.append(self._sync_one(child, mode, force=force))
            elif not self.catalog[key].available:
                self.repo.set_sync_state(
                    key,
                    status="blocked_capability",
                    last_error_code="capability_missing",
                    last_error_message=self.catalog[key].caveat,
                )
                results.append({"metric_key": key, "status": "blocked_capability"})
            else:
                results.append(self._sync_one(key, mode, force=force))
        success_count = sum(row["status"] == "success" for row in results)
        hard_failures = [row for row in results if row["status"] not in {"success", "blocked_capability", "blocked_auth", "deferred"}]
        if hard_failures and not success_count:
            overall = "failed"
        elif not success_count and any(row["status"] == "blocked_auth" for row in results):
            overall = "blocked_auth"
        elif hard_failures or any(row["status"] in {"blocked_capability", "blocked_auth"} for row in results):
            overall = "partial"
        else:
            overall = "success"
        con = self.repo.connect()
        try:
            con.execute(
                "UPDATE sync_runs SET ended_at=?,status=?,metrics_json=? WHERE run_id=?",
                (utc_now(), overall, json.dumps(results, ensure_ascii=False), run_id),
            )
            con.commit()
        finally:
            con.close()
        self.repo.prune(self.settings.retention_days)
        return {"run_id": run_id, "status": overall, "metrics": results}

    async def sync(self, metric_keys: list[str] | None = None, mode: str = "incremental", *, force: bool = False) -> dict:
        # Waiting on one lock serializes scheduled and manual runs instead of
        # allowing a second run to report a misleading partial result.
        async with self._run_lock:
            keys = list(metric_keys) if metric_keys is not None else self.eligible_metrics()
            if len(keys) > self.settings.max_sync_requests_per_cycle:
                keys = keys[: self.settings.max_sync_requests_per_cycle]
            return await asyncio.to_thread(self._sync_sequence, keys, mode, force)

    async def enqueue_job(self, job_id: str, metric_keys: list[str], mode: str) -> None:
        await self._job_queue.put((job_id, metric_keys, mode))
        if self._job_worker is None or self._job_worker.done():
            self._job_worker = asyncio.create_task(self._drain_job_queue(), name="oppo-health-manual-sync-queue")

    async def _drain_job_queue(self) -> None:
        while True:
            try:
                job_id, metric_keys, mode = self._job_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                self.repo.update_job(job_id, "running", started=True)
                result = await self.sync(metric_keys, mode, force=True)
                self.repo.update_job(job_id, result["status"], result=result)
            except Exception:
                log.exception("manual sync job failed")
                self.repo.update_job(job_id, "failed", result={"status": "failed"})
            finally:
                self._job_queue.task_done()

    async def scheduler(self, stop: asyncio.Event) -> None:
        # First launch performs the configured historical backfill. Once a
        # metric is complete, _sync_one automatically falls back to the recent
        # lookback window for the ten-minute incremental rounds.
        await asyncio.sleep(1)
        interval = self.settings.sync_interval_minutes * 60
        next_cycle = time.monotonic()
        while not stop.is_set():
            try:
                await self.sync(mode="backfill", force=False)
            except Exception:
                log.exception("scheduled sync cycle failed")
            next_cycle += interval
            delay = max(0.0, next_cycle - time.monotonic())
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                # If a full cycle took longer than the interval, schedule the
                # next eligible pass immediately without overlapping the run.
                pass