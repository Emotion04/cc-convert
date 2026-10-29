# SPDX-License-Identifier: Apache-2.0
"""SQLite persistence: schema, idempotent upserts, cross-connection reads."""

import sqlite3

import pytest

from cc_convert.usage.store import SCHEMA_VERSION, UsageStore


def _row(**overrides):
    base = {
        "ts": 1_700_000_000,
        "instance_id": "inst",
        "model": "gpt-x",
        "client_model": "claude-x",
        "endpoint": "/v1/messages",
        "prompt_class": "0-32k",
        "input_tokens": 10,
        "output_tokens": 5,
        "requests": 1,
    }
    base.update(overrides)
    return base


@pytest.fixture
def store(tmp_path):
    s = UsageStore(tmp_path / "usage.db")
    yield s
    s.close()


class TestSchema:
    def test_schema_version_recorded(self, store):
        row = store._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        assert row == (str(SCHEMA_VERSION),)

    def test_future_version_refused(self, tmp_path):
        db = tmp_path / "future.db"
        fresh = UsageStore(db)
        fresh._conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        fresh._conn.commit()
        fresh.close()
        with pytest.raises(RuntimeError, match="newer"):
            UsageStore(db)

    def test_reopening_is_idempotent(self, tmp_path):
        db = tmp_path / "usage.db"
        s1 = UsageStore(db)
        s1.upsert_buckets([_row()])
        s1.close()
        s2 = UsageStore(db)
        rows = s2.query(0, 4_000_000_000)
        assert rows[0]["requests"] == 1
        s2.close()


class TestUpsert:
    def test_same_key_adds_up(self, store):
        store.upsert_buckets([_row(input_tokens=10, requests=1)])
        store.upsert_buckets([_row(input_tokens=15, requests=2)])
        rows = store.query(0, 4_000_000_000)
        assert len(rows) == 1
        assert rows[0]["input_tokens"] == 25
        assert rows[0]["requests"] == 3

    def test_different_keys_separate_rows(self, store):
        store.upsert_buckets(
            [
                _row(model="a"),
                _row(model="b"),
                _row(model="a", prompt_class="32-128k"),
            ]
        )
        rows = store.query(0, 4_000_000_000)
        assert len(rows) == 3

    def test_query_range_and_grouping(self, store):
        store.upsert_buckets(
            [
                _row(ts=1000, requests=1),
                _row(ts=2000, model="b", requests=2),
                _row(ts=3000, requests=4),
            ]
        )
        detail = store.query(0, 2500)
        assert sum(r["requests"] for r in detail) == 3
        grouped = store.query(0, 4_000_000_000, group_by="model")
        assert {r["key"]: r["requests"] for r in grouped} == {"gpt-x": 5, "b": 2}

    def test_instance_filter(self, store):
        store.upsert_buckets([_row(instance_id="one"), _row(instance_id="two")])
        rows = store.query(0, 4_000_000_000, instance_id="two")
        assert len(rows) == 1
        assert rows[0]["instance_id"] == "two"


class TestConcurrency:
    def test_two_connections_write_different_instances(self, tmp_path):
        db = tmp_path / "usage.db"
        s1 = UsageStore(db)
        s2 = UsageStore(db)
        s1.upsert_buckets([_row(instance_id="one", requests=1)])
        s2.upsert_buckets([_row(instance_id="two", requests=1)])
        s1.close()
        s2.close()
        check = UsageStore(db)
        rows = check.query(0, 4_000_000_000)
        assert sum(r["requests"] for r in rows) == 2
        check.close()

    def test_survives_reopen(self, tmp_path):
        db = tmp_path / "usage.db"
        s1 = UsageStore(db)
        s1.upsert_buckets([_row(input_tokens=42)])
        s1.close()
        s2 = UsageStore(db)
        assert s2.query(0, 4_000_000_000)[0]["input_tokens"] == 42
        s2.close()


class TestWAL:
    def test_wal_mode_enabled(self, store):
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
