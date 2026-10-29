# SPDX-License-Identifier: Apache-2.0
"""In-memory usage ledger plus the worker-side collector that persists it.

The request path only ever touches :class:`UsageLedger` — one dict
accumulation under a lock, no IO (docs/用量统计设计.md §3.3). The
:class:`UsageCollector` owns the SQLite store and a background thread that
drains dirty buckets every few seconds; a failed flush keeps the data in
memory and retries next cycle, so accounting problems can never take the
proxy down.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from cc_convert.usage.recording import NormalizedUsage, normalize_usage, prompt_class

logger = logging.getLogger(__name__)

#: Bucket granularity: 15 minutes (docs/用量统计设计.md §3.2).
BUCKET_SECONDS = 900

#: Additive counter fields stored per bucket.
BUCKET_FIELDS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read",
    "cache_write",
    "cache_write_1h",
    "cache_write_est",
    "requests",
    "errors",
    "unreported",
    "duration_ms",
)

_DIMENSIONS = ("ts", "instance_id", "model", "client_model", "endpoint", "prompt_class")


def _empty_bucket() -> dict[str, int]:
    return dict.fromkeys(BUCKET_FIELDS, 0)


class UsageLedger:
    """Accumulates usage into (15 min, instance, model, endpoint, 档位) buckets."""

    def __init__(self, instance_id: str):
        self.instance_id = instance_id
        self._buckets: dict[tuple, dict[str, int]] = {}
        self._lock = threading.Lock()

    def record(
        self,
        *,
        endpoint: str,
        model: str,
        client_model: str,
        usage: NormalizedUsage | None,
        requests: int = 0,
        errors: int = 0,
        unreported: int = 0,
        duration_ms: int = 0,
        ts: float | None = None,
    ) -> tuple:
        """Add one accounting entry. Returns the bucket key it landed in."""
        now = time.time() if ts is None else ts
        bucket_ts = int(now) // BUCKET_SECONDS * BUCKET_SECONDS
        u = usage or NormalizedUsage()
        key = (
            bucket_ts,
            self.instance_id,
            model or "",
            client_model or "",
            endpoint,
            prompt_class(u.total_prompt),
        )
        with self._lock:
            bucket = self._buckets.setdefault(key, _empty_bucket())
            bucket["input_tokens"] += u.input_tokens
            bucket["output_tokens"] += u.output_tokens
            bucket["reasoning_tokens"] += u.reasoning_tokens
            bucket["cache_read"] += u.cache_read
            bucket["cache_write"] += u.cache_write
            bucket["cache_write_1h"] += u.cache_write_1h
            if u.ttl_source != "response":
                bucket["cache_write_est"] += u.cache_write
            bucket["requests"] += requests
            bucket["errors"] += errors
            bucket["unreported"] += unreported
            bucket["duration_ms"] += duration_ms
        return key

    def flush(self) -> list[dict[str, Any]]:
        """Return all dirty buckets as rows for the store, clearing memory."""
        with self._lock:
            items = self._buckets
            self._buckets = {}
        rows = []
        for key, values in items.items():
            row = dict(zip(_DIMENSIONS, key))
            row.update(values)
            rows.append(row)
        return rows

    def pending(self) -> int:
        with self._lock:
            return len(self._buckets)


@dataclass
class _TodayTotals:
    date: str = field(default_factory=lambda: date.today().isoformat())
    requests: int = 0
    errors: int = 0
    unreported: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "requests": self.requests,
            "errors": self.errors,
            "unreported": self.unreported,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
        }


class RequestRecorder:
    """Per-client-request accumulator handed out by :class:`UsageCollector`.

    One client request may hit the upstream several times (server tool
    loop); call :meth:`upstream` once per upstream usage report and
    :meth:`finish` exactly once when the request ends. Every method swallows
    its own exceptions: accounting must never break the proxy.
    """

    def __init__(self, collector: "UsageCollector", endpoint: str, model: str, client_model: str):
        self._collector = collector
        self._endpoint = endpoint
        self._model = model
        self._client_model = client_model
        self._started = time.monotonic()
        self._usage = NormalizedUsage()
        self._saw_usage = False

    def upstream(self, usage: Any) -> None:
        """Accumulate one upstream usage report (any wire shape).

        Used when one client request makes several upstream calls (server
        tool loop): every call's tokens add up.
        """
        try:
            normalized = normalize_usage(usage)
        except Exception:  # defensive: upstreams send arbitrary JSON
            logger.warning("Failed to normalize upstream usage", exc_info=True)
            return
        if normalized is None:
            return
        self._usage.input_tokens += normalized.input_tokens
        self._usage.output_tokens += normalized.output_tokens
        self._usage.reasoning_tokens += normalized.reasoning_tokens
        self._usage.cache_read += normalized.cache_read
        self._usage.cache_write += normalized.cache_write
        self._usage.cache_write_1h += normalized.cache_write_1h
        self._saw_usage = True

    def latest(self, usage: Any) -> None:
        """Replace the accumulated usage with *usage*.

        Used on streaming paths, where a single upstream call may surface
        its usage more than once (first chunk and the trailing include_usage
        chunk) — the final report wins instead of being counted twice.
        """
        try:
            normalized = normalize_usage(usage)
        except Exception:  # defensive: upstreams send arbitrary JSON
            logger.warning("Failed to normalize upstream usage", exc_info=True)
            return
        if normalized is None:
            return
        self._usage = normalized
        self._saw_usage = True

    def finish(
        self,
        *,
        requests: int = 1,
        errors: int = 0,
        unreported: int | None = None,
    ) -> None:
        """Write the request into the ledger.

        ``unreported`` defaults to "1 for a successful counted request that
        never saw any usage" — never fabricate tokens for it.
        """
        try:
            if unreported is None:
                unreported = 1 if (requests and not errors and not self._saw_usage) else 0
            duration_ms = int((time.monotonic() - self._started) * 1000)
            usage = self._usage if self._saw_usage else None
            self._collector._record(
                endpoint=self._endpoint,
                model=self._model,
                client_model=self._client_model,
                usage=usage,
                requests=requests,
                errors=errors,
                unreported=unreported,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.warning("Failed to record usage", exc_info=True)


class UsageCollector:
    """Worker-side glue: ledger + SQLite store + background flush thread."""

    def __init__(
        self,
        instance_id: str,
        db_path: str | Path | None = None,
        flush_interval: float = 5.0,
    ):
        self.instance_id = instance_id
        self.ledger = UsageLedger(instance_id)
        self.flush_interval = flush_interval
        self._db_path = db_path
        self._store: Any = None
        self._store_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._today = _TodayTotals()
        self._today_lock = threading.Lock()
        # Store and thread are created lazily on the first record: apps that
        # never serve proxy traffic (most tests, health-only workers) then
        # never open a database file.

    # -- public API --------------------------------------------------------

    def recorder(self, endpoint: str, model: str, client_model: str) -> RequestRecorder:
        """Create a per-request recorder. Cheap; safe to call per request."""
        return RequestRecorder(self, endpoint, model, client_model)

    def summary(self) -> dict[str, Any]:
        """Today's totals for the status endpoint (in-memory, no DB read)."""
        with self._today_lock:
            if self._today.date != date.today().isoformat():
                self._today = _TodayTotals()
            return self._today.to_dict()

    def flush_once(self) -> None:
        """Drain the ledger into SQLite once. Safe to call from anywhere."""
        rows = self.ledger.flush()
        if not rows:
            return
        store = self._ensure_store()
        if store is None:
            logger.warning(
                "Usage store unavailable, dropping %d usage bucket(s)", len(rows)
            )
            return
        try:
            store.upsert_buckets(rows)
        except Exception:
            logger.warning("Usage flush failed, %d bucket(s) lost", len(rows), exc_info=True)

    def close(self) -> None:
        """Stop the flush thread and persist whatever is left."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.flush_once()
        with self._store_lock:
            if self._store is not None:
                try:
                    self._store.close()
                except Exception:
                    logger.warning("Failed to close usage store", exc_info=True)
                self._store = None

    # -- internals ---------------------------------------------------------

    def _record(self, **kwargs: Any) -> None:
        """Single choke point: ledger + today's summary. Called by recorders."""
        usage: NormalizedUsage | None = kwargs.get("usage")
        self.ledger.record(**kwargs)
        with self._today_lock:
            if self._today.date != date.today().isoformat():
                self._today = _TodayTotals()
            t = self._today
            t.requests += kwargs.get("requests", 0)
            t.errors += kwargs.get("errors", 0)
            t.unreported += kwargs.get("unreported", 0)
            if usage is not None:
                t.input_tokens += usage.input_tokens
                t.output_tokens += usage.output_tokens
                t.cache_read += usage.cache_read
                t.cache_write += usage.cache_write + usage.cache_write_1h
        if self._thread is None and not self._stop.is_set():
            self._start_thread()

    def _start_thread(self) -> None:
        def _loop() -> None:
            while not self._stop.wait(self.flush_interval):
                try:
                    self.flush_once()
                except Exception:  # the loop must survive anything
                    logger.warning("Usage flush loop error", exc_info=True)

        if self._stop.is_set():
            return
        with self._thread_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=_loop, name="usage-flush", daemon=True
            )
            self._thread.start()

    def _ensure_store(self) -> Any:
        with self._store_lock:
            if self._store is not None:
                return self._store
            if self._db_path is None:
                return None
            try:
                from cc_convert.usage.store import UsageStore

                self._store = UsageStore(self._db_path)
                return self._store
            except Exception:
                logger.warning(
                    "Failed to open usage store at %s", self._db_path, exc_info=True
                )
                # Don't retry on every flush: a broken path stays broken, and
                # retrying would spam the log.
                self._db_path = None
                return None
