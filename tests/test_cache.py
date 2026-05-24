"""Unit tests for `benchmarkoor_fetch.client.cache`.

Content-addressed disk cache. Tests pin the key construction (so a hit is
guaranteed-identical bytes), the read/write/skip flow, and the bypass paths.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import responses

from benchmarkoor_fetch import BenchmarkoorClient
from benchmarkoor_fetch.client import cache as cache_module

BENCHMARKOOR_HOST = "https://benchmarkoor-api.core.ethpandaops.io"
BASE_URL = f"{BENCHMARKOOR_HOST}/api/v1/index/query"
FILES_BASE_URL = f"{BENCHMARKOOR_HOST}/api/v1/files"


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("BENCHMARKOOR_TOKEN", "test-token")
    return "test-token"


# --------------------------------------------------------------------------- #
# Scenario #45: runs key shape
# --------------------------------------------------------------------------- #


def test_runs_cache_key_shape(tmp_path: Path) -> None:
    """Scenario #45: runs key encodes suite + start_date (no window collisions)."""
    cache = cache_module.DiskCache(root=tmp_path)
    key_all = cache.runs_key(suite_hash="0xbbb222")
    assert Path(key_all) == tmp_path / "0xbbb222" / "runs-all.json"

    key_from = cache.runs_key(suite_hash="0xbbb222", start_date="2026-05-18")
    assert Path(key_from) == tmp_path / "0xbbb222" / "runs-from-2026-05-18.json"


# --------------------------------------------------------------------------- #
# Scenario #46: test-stats key shape
# --------------------------------------------------------------------------- #


def test_test_stats_cache_key_shape(tmp_path: Path) -> None:
    """Scenario #46: <suite>/test_stats/<run_id>.parquet."""
    cache = cache_module.DiskCache(root=tmp_path)
    key = cache.test_stats_key(suite_hash="0xbbb222", run_id="run-001-full")
    expected = tmp_path / "0xbbb222" / "test_stats" / "run-001-full.parquet"
    assert Path(key) == expected


# --------------------------------------------------------------------------- #
# Scenario #47: summary key shape
# --------------------------------------------------------------------------- #


def test_summary_cache_key_shape(tmp_path: Path) -> None:
    """Scenario #47: <suite>/summary.json."""
    cache = cache_module.DiskCache(root=tmp_path)
    key = cache.summary_key(suite_hash="0xbbb222")
    expected = tmp_path / "0xbbb222" / "summary.json"
    assert Path(key) == expected


# --------------------------------------------------------------------------- #
# Scenario #48: miss writes, hit reads
# --------------------------------------------------------------------------- #


def test_miss_writes_then_hit_reads_without_fetcher(tmp_path: Path) -> None:
    """Scenario #48: first call writes the cache file; second call skips the fetcher."""
    cache = cache_module.DiskCache(root=tmp_path)
    calls = {"count": 0}

    def fetcher() -> dict[str, int]:
        calls["count"] += 1
        return {"value": 42}

    key = tmp_path / "demo.json"
    out1 = cache.get_or_fetch_json(key, fetcher)
    out2 = cache.get_or_fetch_json(key, fetcher)
    assert out1 == out2 == {"value": 42}
    assert calls["count"] == 1, "Fetcher should be called once across two reads."
    assert key.exists()


# --------------------------------------------------------------------------- #
# Scenario #51: enabled=False bypasses read and write
# --------------------------------------------------------------------------- #


def test_disabled_cache_bypasses_read_and_write(tmp_path: Path) -> None:
    """Scenario #51: enabled=False → fetcher runs every time and disk stays empty."""
    cache = cache_module.DiskCache(root=tmp_path, enabled=False)
    calls = {"count": 0}

    def fetcher() -> dict[str, int]:
        calls["count"] += 1
        return {"value": 7}

    key = tmp_path / "demo.json"
    cache.get_or_fetch_json(key, fetcher)
    cache.get_or_fetch_json(key, fetcher)

    assert calls["count"] == 2
    assert list(tmp_path.iterdir()) == [], "Disabled cache must never write to disk."


# --------------------------------------------------------------------------- #
# Scenario #52: same window = one cache file = one HTTP call
# --------------------------------------------------------------------------- #


@responses.activate
def test_same_window_shares_runs_cache_file(tmp_path: Path, token: str) -> None:
    """Scenario #52: same suite + same start_date = one cache file, one HTTP call.

    Server-side `timestamp` filtering means different start_dates issue
    different wire requests; only repeat fetches for the same start_date are
    served from cache.
    """
    body = {
        "data": [
            {"run_id": "run-001-full", "timestamp": 1779181200},
            {"run_id": "run-002-full", "timestamp": 1779354000},
        ]
    }
    responses.add(responses.GET, f"{BASE_URL}/runs", json=body, status=200)
    responses.add(responses.GET, f"{BASE_URL}/runs", json=body, status=200)

    client = BenchmarkoorClient(token=token, cache_dir=tmp_path)
    first = client.list_runs(
        suite_hash="0xbbb222", start_date="2026-05-18", end_date="2026-05-19"
    )
    second = client.list_runs(
        suite_hash="0xbbb222", start_date="2026-05-18", end_date="2026-05-21"
    )

    # Both reads pass through the same `runs-from-2026-05-18.json` cache file.
    cache_file = tmp_path / "0xbbb222" / "runs-from-2026-05-18.json"
    assert cache_file.is_file()
    assert [r["run_id"] for r in first] == ["run-001-full"]
    assert [r["run_id"] for r in second] == ["run-001-full", "run-002-full"]
    assert len(responses.calls) == 1, (
        f"second list_runs should hit cache; got {len(responses.calls)} HTTP calls"
    )


# --------------------------------------------------------------------------- #
# Scenario #53: discovery is not wrapped
# --------------------------------------------------------------------------- #


@responses.activate
def test_resolve_suite_never_writes_to_cache(tmp_path: Path, token: str) -> None:
    """Scenario #53: resolve_suite never writes a /suites artifact to disk."""
    responses.add(
        responses.GET,
        f"{BASE_URL}/suites",
        json={
            "data": [
                {
                    "suite_hash": "0xaaa111",
                    "name": "jochemnet-20260518-amsterdam-compute",
                    "indexed_at": "2026-05-18T03:14:22Z",
                }
            ]
        },
        status=200,
    )

    client = BenchmarkoorClient(token=token, cache_dir=tmp_path)
    client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")

    walked = list(tmp_path.rglob("*"))
    for p in walked:
        assert "suites" not in p.name, (
            f"Discovery must not be cached; found cache artifact {p}."
        )


# --------------------------------------------------------------------------- #
# Scenario #54: cache stores raw response (round-trips wire columns)
# --------------------------------------------------------------------------- #


@responses.activate
def test_cache_stores_raw_response(tmp_path: Path, token: str) -> None:
    """Scenario #54: cached parquet round-trips the raw wire columns."""
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={"total": 1},
        status=200,
        match=[
            responses.matchers.query_param_matcher({"limit": "0"}, strict_match=False)
        ],
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={
            "data": [
                {
                    "run_id": "run-001-full",
                    "client": "geth",
                    "test_name": "t1",
                    "test_time_ns": 1_234_000_000,
                    "run_start": 1779074400,
                }
            ]
        },
        status=200,
        match=[
            responses.matchers.query_param_matcher({"offset": "0"}, strict_match=False)
        ],
    )

    client = BenchmarkoorClient(token=token, cache_dir=tmp_path)
    client.fetch_test_stats(
        run_ids=["run-001-full"], page_size=10, suite_hash="0xbbb222"
    )

    cache_file = tmp_path / "0xbbb222" / "test_stats" / "run-001-full.parquet"
    assert cache_file.exists()
    cached = pd.read_parquet(cache_file)
    # Cached frame keeps the wire column names; the rename happens post-cache.
    assert "test_time_ns" in cached.columns
    assert "test_runtime_ms" not in cached.columns


# --------------------------------------------------------------------------- #
# Scenario #55: verbose=True emits `miss: <key>` once per miss
# --------------------------------------------------------------------------- #


def test_verbose_emits_one_miss_per_cache_miss(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario #55: verbose mode prints `miss: <key>` once per miss; hits silent."""
    cache = cache_module.DiskCache(root=tmp_path, verbose=True)

    def fetcher() -> dict[str, int]:
        return {"value": 1}

    key = tmp_path / "demo.json"
    cache.get_or_fetch_json(key, fetcher)
    captured_miss = capsys.readouterr().err
    assert "miss:" in captured_miss
    assert str(key) in captured_miss

    cache.get_or_fetch_json(key, fetcher)
    captured_hit = capsys.readouterr().err
    assert "miss:" not in captured_hit


# --------------------------------------------------------------------------- #
# Scenario #55b: verbose=True emits `hit: <key>` once per cache hit
# --------------------------------------------------------------------------- #


def test_verbose_emits_one_hit_per_cache_hit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario #55b: verbose mode prints `hit: <key>` whenever a cached
    entry satisfies a read."""
    cache = cache_module.DiskCache(root=tmp_path, verbose=True)

    def fetcher() -> dict[str, int]:
        return {"value": 1}

    key = tmp_path / "demo.json"
    cache.get_or_fetch_json(key, fetcher)
    capsys.readouterr()  # drop the miss line from the first call

    cache.get_or_fetch_json(key, fetcher)
    captured_hit = capsys.readouterr().err
    assert "hit:" in captured_hit
    assert str(key) in captured_hit
