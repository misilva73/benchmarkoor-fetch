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

BASE_URL = "https://benchmarkoor-api.core.ethpandaops.io"


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("BENCHMARKOOR_TOKEN", "test-token")
    return "test-token"


# --------------------------------------------------------------------------- #
# Scenario #45: runs key shape
# --------------------------------------------------------------------------- #


def test_runs_cache_key_shape(tmp_path: Path) -> None:
    """Scenario #45: runs key is just `{suite}/runs.json` — filters are client-side."""
    cache = cache_module.DiskCache(root=tmp_path)
    key = cache.runs_key(suite_hash="0xbbb222")
    expected = tmp_path / "0xbbb222" / "runs.json"
    assert Path(key) == expected


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
# Scenario #52: different windows share a single runs cache file
# --------------------------------------------------------------------------- #


@responses.activate
def test_different_windows_share_runs_cache_file(tmp_path: Path, token: str) -> None:
    """Scenario #52: same suite + different windows = one cache file, one HTTP call.

    Filters apply client-side, so the wire payload is identical regardless of
    the window — the second call must be served from cache.
    """
    body = {
        "data": [
            {
                "run_id": "run-001-full",
                "suite_hash": "0xbbb222",
                "start_ts": "2026-05-18T10:00:00Z",
                "run_type": "full",
            },
            {
                "run_id": "run-002-full",
                "suite_hash": "0xbbb222",
                "start_ts": "2026-05-20T10:00:00Z",
                "run_type": "full",
            },
        ]
    }
    responses.add(responses.GET, f"{BASE_URL}/runs", json=body, status=200)
    responses.add(responses.GET, f"{BASE_URL}/runs", json=body, status=200)

    client = BenchmarkoorClient(token=token, cache_dir=tmp_path)
    first = client.list_runs(
        suite_hash="0xbbb222", start_date="2026-05-18", end_date="2026-05-19"
    )
    second = client.list_runs(
        suite_hash="0xbbb222", start_date="2026-05-20", end_date="2026-05-21"
    )

    assert [r["run_id"] for r in first] == ["run-001-full"]
    assert [r["run_id"] for r in second] == ["run-002-full"]

    cache_file = tmp_path / "0xbbb222" / "runs.json"
    assert cache_file.is_file(), "expected a single runs.json cache file"
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
                    "name": "x",
                    "network": "jochemnet",
                    "fork": "amsterdam",
                    "test_type": "compute",
                    "indexed_at": "2026-05-18T03:14:22Z",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 10,
        },
        status=200,
    )

    client = BenchmarkoorClient(token=token, cache_dir=tmp_path)
    client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")

    # No subdirectory or file mentioning "suites" must appear in the cache.
    walked = list(tmp_path.rglob("*"))
    for p in walked:
        assert "suites" not in p.name, (
            f"Discovery must not be cached; found cache artifact {p}."
        )


# --------------------------------------------------------------------------- #
# Scenario #54: cache stores raw response (round-trips API JSON columns)
# --------------------------------------------------------------------------- #


@responses.activate
def test_cache_stores_raw_response(tmp_path: Path, token: str) -> None:
    """Scenario #54: cached parquet round-trips the raw API columns."""
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={"data": [], "total": 1, "page": 1, "page_size": 10},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={
            "data": [
                {
                    "run_id": "run-001-full",
                    "client_name": "geth",
                    "test_title": "t1",
                    "run_duration_ms": 1234,
                    "ingestion_timestamp": "2026-05-18T03:20:00Z",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 10,
        },
        status=200,
    )

    client = BenchmarkoorClient(token=token, cache_dir=tmp_path)
    client.fetch_test_stats(
        run_ids=["run-001-full"], page_size=10, suite_hash="0xbbb222"
    )

    cache_file = tmp_path / "0xbbb222" / "test_stats" / "run-001-full.parquet"
    assert cache_file.exists()
    cached = pd.read_parquet(cache_file)
    # Cached frame must use the wire column name, not the renamed one.
    assert "run_duration_ms" in cached.columns
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
