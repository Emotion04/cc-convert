# SPDX-License-Identifier: Apache-2.0
"""SQLite persistence for usage buckets.

Only SQL lives here — no business rules, no request-path code (docs/用量统计
实施计划.md §2). Every Worker writes only rows whose ``instance_id`` is its
own, so the primary key never collides across processes; WAL mode plus a
busy timeout lets Worker writes and Manager reads proceed concurrently.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_bucket (
    ts            INTEGER NOT NULL,   -- epoch seconds, floored to 15 min
    instance_id   TEXT    NOT NULL,
    vendor        TEXT    NOT NULL DEFAULT '',
    model         TEXT    NOT NULL,
    client_model  TEXT    NOT NULL,
    endpoint      TEXT    NOT NULL,
    prompt_class  TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    cache_write_1h INTEGER NOT NULL DEFAULT 0,
    ttl_source    TEXT    NOT NULL DEFAULT 'config',
    cache_write_est INTEGER NOT NULL DEFAULT 0,
    requests      INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    unreported    INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts, instance_id, model, client_model, endpoint, prompt_class)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_usage_ts       ON usage_bucket(ts);
CREATE INDEX IF NOT EXISTS idx_usage_instance ON usage_bucket(instance_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_model    ON usage_bucket(model, ts);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Counter columns summed on conflict; key columns are part of the primary key.
_COUNTERS = (
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

_UPSERT_SQL = """
INSERT INTO usage_bucket ({cols})
VALUES ({placeholders})
ON CONFLICT (ts, instance_id, model, client_model, endpoint, prompt_class) DO UPDATE SET
{updates}
""".format(
    cols=", ".join(("ts", "instance_id", "vendor", "model", "client_model", "endpoint", "prompt_class") + _COUNTERS),
    placeholders=", ".join("?" for _ in range(7 + len(_COUNTERS))),
    updates=", ".join(f"{c} = {c} + excluded.{c}" for c in _COUNTERS),
)


class UsageStore:
    """Thin SQLite wrapper around the ``usage_bucket`` table."""

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=5)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()
        elif int(row[0]) > SCHEMA_VERSION:
            # Never write into a database produced by a newer build — the
            # upserts would silently corrupt an unknown schema.
            self._conn.close()
            raise RuntimeError(
                f"usage.db schema version {row[0]} is newer than supported "
                f"{SCHEMA_VERSION}; refusing to write"
            )

    def upsert_buckets(self, rows: list[dict[str, Any]]) -> None:
        """Merge accumulated buckets. Additive on every counter column."""
        params = []
        for row in rows:
            params.append(
                (
                    row["ts"],
                    row["instance_id"],
                    row.get("vendor", ""),
                    row["model"],
                    row["client_model"],
                    row["endpoint"],
                    row["prompt_class"],
                    *(row.get(c, 0) for c in _COUNTERS),
                )
            )
        with self._conn:
            self._conn.executemany(_UPSERT_SQL, params)

    def query(
        self,
        from_ts: int,
        to_ts: int,
        group_by: str | None = None,
        instance_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate buckets over ``[from_ts, to_ts)``.

        Without ``group_by`` every dimension column is returned (one row per
        bucket key, counters summed across flushes). With ``group_by`` (one
        of the bucket's text columns) rows collapse to that dimension.
        """
        _dims = ("ts", "instance_id", "vendor", "model", "client_model", "endpoint", "prompt_class")
        sums = ", ".join(f"SUM({c}) AS {c}" for c in _COUNTERS)
        params: list[Any] = [from_ts, to_ts]
        sql = "SELECT "
        if group_by:
            sql += f"{group_by} AS key, {sums} FROM usage_bucket WHERE ts >= ? AND ts < ?"
        else:
            dims = ", ".join(_dims)
            sql += f"{dims}, {sums} FROM usage_bucket WHERE ts >= ? AND ts < ?"
        if instance_id is not None:
            sql += " AND instance_id = ?"
            params.append(instance_id)
        if group_by:
            sql += f" GROUP BY {group_by} ORDER BY {group_by}"
        else:
            sql += " GROUP BY " + ", ".join(_dims)
        cursor = self._conn.execute(sql, params)
        return [dict(zip((c[0] for c in cursor.description), row)) for row in cursor.fetchall()]

    def close(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._conn.close()
