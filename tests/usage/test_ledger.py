# SPDX-License-Identifier: Apache-2.0
"""Ledger bucketing and the per-request recorder."""

from cc_convert.usage.ledger import BUCKET_SECONDS, RequestRecorder, UsageCollector, UsageLedger
from cc_convert.usage.recording import NormalizedUsage


def _usage(**kw) -> NormalizedUsage:
    defaults = dict(input_tokens=10, output_tokens=5)
    defaults.update(kw)
    return NormalizedUsage(**defaults)


class TestLedger:
    def test_same_bucket_accumulates(self):
        ledger = UsageLedger("inst")
        for _ in range(3):
            ledger.record(
                endpoint="/v1/messages",
                model="m",
                client_model="cm",
                usage=_usage(),
                requests=1,
            )
        rows = ledger.flush()
        assert len(rows) == 1
        row = rows[0]
        assert row["requests"] == 3
        assert row["input_tokens"] == 30
        assert row["output_tokens"] == 15

    def test_15_minute_bucketing(self):
        ledger = UsageLedger("inst")
        base = 1_700_000_000 - (1_700_000_000 % BUCKET_SECONDS)
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=_usage(), ts=base,
        )
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=_usage(), ts=base + BUCKET_SECONDS,
        )
        rows = ledger.flush()
        assert len(rows) == 2
        assert {r["ts"] for r in rows} == {base, base + BUCKET_SECONDS}

    def test_dimensions_split_buckets(self):
        ledger = UsageLedger("inst")
        for endpoint in ("/v1/messages", "/v1/chat/completions"):
            ledger.record(
                endpoint=endpoint, model="m", client_model="cm", usage=_usage()
            )
        for model in ("m1", "m2"):
            ledger.record(
                endpoint="/v1/messages", model=model, client_model="cm", usage=_usage()
            )
        rows = ledger.flush()
        assert len(rows) == 4
        assert len({(r["endpoint"], r["model"]) for r in rows}) == 4

    def test_prompt_class_is_part_of_the_key(self):
        ledger = UsageLedger("inst")
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=_usage(input_tokens=10),
        )
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=_usage(input_tokens=200_000),
        )
        rows = ledger.flush()
        classes = {r["prompt_class"] for r in rows}
        assert classes == {"0-32k", "200-400k"}

    def test_unreported_and_errors(self):
        ledger = UsageLedger("inst")
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=None, requests=1, unreported=1,
        )
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=None, errors=1,
        )
        row = ledger.flush()[0]
        assert row["requests"] == 1
        assert row["unreported"] == 1
        assert row["errors"] == 1
        assert row["input_tokens"] == 0

    def test_flush_clears_memory(self):
        ledger = UsageLedger("inst")
        ledger.record(endpoint="/v1/messages", model="m", client_model="cm", usage=_usage())
        assert ledger.pending() == 1
        assert ledger.flush()
        assert ledger.pending() == 0
        assert ledger.flush() == []

    def test_cache_write_est_marks_config_ttl(self):
        ledger = UsageLedger("inst")
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=_usage(cache_write=50, ttl_source="config"),
        )
        # Different prompt size → different bucket, so the two TTL sources
        # don't merge in one row.
        ledger.record(
            endpoint="/v1/messages", model="m", client_model="cm",
            usage=_usage(input_tokens=40_000, cache_write=70, cache_write_1h=20,
                         ttl_source="response"),
        )
        rows = ledger.flush()
        by_class = {r["prompt_class"]: r for r in rows}
        assert by_class["0-32k"]["cache_write_est"] == 50
        assert by_class["32-128k"]["cache_write_est"] == 0
        assert by_class["32-128k"]["cache_write_1h"] == 20


class _FakeCollector:
    def _record(self, **kwargs):
        self.calls = getattr(self, "calls", [])
        self.calls.append(kwargs)


class TestRequestRecorder:
    def test_upstream_accumulates_across_calls(self):
        collector = _FakeCollector()
        rec = RequestRecorder(collector, "/v1/messages", "m", "cm")
        rec.upstream({"prompt_tokens": 100, "completion_tokens": 10})
        rec.upstream({"prompt_tokens": 50, "completion_tokens": 5})
        rec.finish()
        call = collector.calls[0]
        usage = call["usage"]
        assert usage.input_tokens == 150
        assert usage.output_tokens == 15
        assert call["requests"] == 1

    def test_latest_replaces(self):
        collector = _FakeCollector()
        rec = RequestRecorder(collector, "/v1/messages", "m", "cm")
        rec.latest({"prompt_tokens": 100, "completion_tokens": 10})
        rec.latest({"prompt_tokens": 130, "completion_tokens": 12})
        rec.finish()
        usage = collector.calls[0]["usage"]
        assert usage.input_tokens == 130
        assert usage.output_tokens == 12

    def test_no_usage_counts_as_unreported(self):
        collector = _FakeCollector()
        rec = RequestRecorder(collector, "/v1/messages", "m", "cm")
        rec.finish()
        call = collector.calls[0]
        assert call["unreported"] == 1
        assert call["usage"] is None

    def test_error_request_not_unreported(self):
        collector = _FakeCollector()
        rec = RequestRecorder(collector, "/v1/messages", "m", "cm")
        rec.finish(errors=1)
        assert collector.calls[0]["unreported"] == 0
        assert collector.calls[0]["errors"] == 1

    def test_never_raises(self):
        collector = _FakeCollector()
        collector._record = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        rec = RequestRecorder(collector, "/v1/messages", "m", "cm")
        rec.upstream({"prompt_tokens": 1, "completion_tokens": 1})
        rec.finish()  # must not raise

    def test_unusable_payloads_are_noops(self):
        collector = _FakeCollector()
        rec = RequestRecorder(collector, "/v1/messages", "m", "cm")
        rec.upstream(None)
        rec.upstream({"nonsense": 1})
        rec.latest(None)
        rec.latest({"nonsense": 1})
        rec.finish()
        call = collector.calls[0]
        assert call["usage"] is None
        assert call["unreported"] == 1


class TestCollector:
    def test_summary_today(self, tmp_path):
        collector = UsageCollector("inst", db_path=tmp_path / "usage.db")
        rec = collector.recorder("/v1/messages", "m", "cm")
        rec.upstream({"prompt_tokens": 100, "completion_tokens": 40})
        rec.finish()
        summary = collector.summary()
        assert summary["requests"] == 1
        assert summary["input_tokens"] == 100
        assert summary["output_tokens"] == 40
        assert summary["unreported"] == 0
        collector.close()

    def test_close_flushes_pending(self, tmp_path):
        db = tmp_path / "usage.db"
        collector = UsageCollector("inst", db_path=db)
        collector.recorder("/v1/messages", "m", "cm").finish()
        collector.close()
        from cc_convert.usage.store import UsageStore

        store = UsageStore(db)
        rows = store.query(0, 4_000_000_000)
        assert rows[0]["requests"] == 1

    def test_no_db_path_stays_in_memory(self):
        collector = UsageCollector("inst", db_path=None)
        collector.recorder("/v1/messages", "m", "cm").finish()
        collector.flush_once()  # no store to write to, must not raise
        collector.close()

    def test_summary_resets_at_midnight(self, tmp_path):
        collector = UsageCollector("inst", db_path=tmp_path / "usage.db")
        collector.recorder("/v1/messages", "m", "cm").finish()
        assert collector.summary()["requests"] == 1
        collector._today.date = "2000-01-01"
        summary = collector.summary()
        assert summary["requests"] == 0
        collector.close()

    def test_unwritable_db_degrades_to_memory(self, tmp_path):
        # A directory where the DB file should be: sqlite cannot open it.
        blocker = tmp_path / "usage.db"
        blocker.mkdir()
        collector = UsageCollector("inst", db_path=blocker)
        collector.recorder("/v1/messages", "m", "cm").finish()
        collector.flush_once()  # must warn, not raise
        collector.close()

    def test_flush_thread_drains_periodically(self, tmp_path):
        from cc_convert.usage.ledger import UsageCollector as _C

        db = tmp_path / "usage.db"
        collector = _C("inst", db_path=db, flush_interval=0.05)
        collector.recorder("/v1/messages", "m", "cm").finish()  # starts thread
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and collector.ledger.pending():
            time.sleep(0.02)
        collector.close()
        assert collector.ledger.pending() == 0
        store = _C("check", db_path=db)  # reopen just to read
        # Reading via a separate store to avoid depending on flush timing.
        store.close()
