# SPDX-License-Identifier: Apache-2.0
"""Manager-side usage summary API and the /usage dashboard page."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cc_convert.main import create_manager_app
from cc_convert.profile_store import ManagerConfig, ProfileStore
from cc_convert.runtime_paths import get_usage_db_file
from cc_convert.usage.store import UsageStore


def _bucket_row(ts_local: datetime, instance="a", model="m1", **counters):
    base = {
        "ts": int(ts_local.timestamp()),
        "instance_id": instance,
        "model": model,
        "client_model": "claude-x",
        "endpoint": "/v1/messages",
        "prompt_class": "0-32k",
    }
    base.update(counters)
    return base


@pytest.fixture
def usage_runtime(monkeypatch, tmp_path):
    """Point CCCONVERT_RUNTIME_DIR at a private dir so this module owns its
    usage.db — other test modules record into the shared sandbox db and hold
    connections open, which would break both isolation and unlink-on-Windows.
    """
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("CCCONVERT_RUNTIME_DIR", str(runtime))
    return runtime


@pytest.fixture
def seeded_db(usage_runtime):
    """Seed today's and yesterday's buckets into this module's usage.db."""
    db = get_usage_db_file()
    store = UsageStore(db)
    now = datetime.now()
    today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday_noon = today_noon - timedelta(days=1)
    store.upsert_buckets(
        [
            _bucket_row(
                today_noon, instance="a", model="m1",
                input_tokens=100, output_tokens=50, cache_read=300, requests=2,
                duration_ms=4000,
            ),
            _bucket_row(
                today_noon, instance="b", model="m2",
                input_tokens=0, output_tokens=5, requests=1, unreported=1,
            ),
            _bucket_row(
                yesterday_noon, instance="a", model="m1",
                input_tokens=7, output_tokens=3, requests=1,
            ),
        ]
    )
    store.close()
    yield db


@pytest.fixture
def client():
    store = ProfileStore(manager=ManagerConfig(port=9999, host="127.0.0.1"), instances=[])
    app = create_manager_app(store, supervisor=object())
    return TestClient(app, headers={"Host": "localhost"})


class TestSummaryApi:
    def test_totals_and_cache_rate(self, client, seeded_db):
        today = datetime.now().strftime("%Y-%m-%d")
        r = client.get(f"/api/usage/summary?from={today}&to={today}")
        assert r.status_code == 200
        body = r.json()
        assert body["granularity"] == "day"
        totals = body["totals"]
        assert totals["requests"] == 3
        # Only today's rows (yesterday's 7 tokens excluded by the range).
        assert totals["input_tokens"] == 100
        assert totals["output_tokens"] == 55
        assert totals["cache_read"] == 300
        assert totals["unreported"] == 1
        # cache_rate = cache_read / (input + cache_read) = 300 / 400
        assert totals["cache_rate"] == round(300 / 400, 4)

    def test_series_day_grouping(self, client, seeded_db):
        r = client.get("/api/usage/summary")
        assert r.status_code == 200
        series = r.json()["series"]
        # Today + yesterday collapse to two day buckets.
        assert len(series) == 2
        today_entry = series[-1]
        assert today_entry["requests"] == 3

    def test_series_hour_grouping(self, client, seeded_db):
        r = client.get("/api/usage/summary?granularity=hour")
        assert r.status_code == 200
        series = r.json()["series"]
        assert len(series) == 2  # noon today, noon yesterday

    def test_group_by_instance(self, client, seeded_db):
        r = client.get("/api/usage/summary?group_by=instance")
        assert r.status_code == 200
        groups = {g["key"]: g for g in r.json()["groups"]}
        assert set(groups) == {"a", "b"}
        assert groups["a"]["requests"] == 3
        assert groups["b"]["unreported"] == 1
        assert groups["a"]["cache_rate"] is not None
        assert groups["b"]["cache_rate"] is None

    def test_group_by_model(self, client, seeded_db):
        r = client.get("/api/usage/summary?group_by=model")
        groups = {g["key"]: g for g in r.json()["groups"]}
        assert set(groups) == {"m1", "m2"}

    def test_empty_database_returns_zeros(self, client, usage_runtime):
        r = client.get("/api/usage/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["totals"]["requests"] == 0
        assert body["series"] == []
        assert body["groups"] == []

    def test_missing_db_returns_zeros(self, client, usage_runtime):
        # The sandbox db may not exist at all on a fresh checkout: the
        # manager must answer with zeros, not 500 (and not create the file).
        db = get_usage_db_file()
        existed = db.exists()
        if existed:
            db.unlink()
        try:
            r = client.get("/api/usage/summary")
            assert r.status_code == 200
            assert r.json()["totals"]["requests"] == 0
            assert not db.exists(), "manager must not create usage.db"
        finally:
            if existed:
                db.touch()

    @pytest.mark.parametrize(
        ("query", "code"),
        [
            ("?group_by=nope", 400),
            ("?granularity=week", 400),
            ("?from=2026/01/01", 400),
            ("?from=2026-01-02&to=2026-01-01", 400),
        ],
    )
    def test_invalid_params(self, client, query, code):
        assert client.get("/api/usage/summary" + query).status_code == code


class TestPages:
    def test_usage_page_renders(self, client):
        r = client.get("/usage")
        assert r.status_code == 200
        assert "用量统计" in r.text

    def test_vendored_chartjs_served(self, client):
        r = client.get("/static/vendor/chart.umd.js")
        assert r.status_code == 200
        assert "Chart.js" in r.text[:200]
