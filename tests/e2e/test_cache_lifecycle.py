"""End-to-end tests for cache lifecycle behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.conftest import (
    CANONICAL_RUN_IDS,
    CANONICAL_SUITE_HASH,
    MockedApi,
    Runner,
)


def test_cold_run_populates_cache(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #22: cold run writes the expected cache keys."""

    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )
    assert result.exit_code == 0, result.stderr

    suite_root = tmp_cache_dir / CANONICAL_SUITE_HASH

    runs_cache = suite_root / "runs.json"
    assert runs_cache.exists() and runs_cache.is_file(), (
        f"no runs cache file at {runs_cache}"
    )

    test_stats_dir = suite_root / "test_stats"
    assert test_stats_dir.exists() and test_stats_dir.is_dir()
    for run_id in CANONICAL_RUN_IDS:
        stats_file = test_stats_dir / f"{run_id}.parquet"
        assert stats_file.exists(), f"missing cache file for {run_id}: {stats_file}"

    summary_file = suite_root / "summary.json"
    assert summary_file.exists(), f"missing summary cache file: {summary_file}"


def test_warm_run_makes_no_http_calls_except_discovery(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
    tmp_path: Path,
) -> None:
    """Scenario #23: warm run only hits /suites; rest comes from cache."""

    # Cold run.
    cold = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )
    assert cold.exit_code == 0, cold.stderr
    cold_runtimes = (tmp_out_dir / "runtimes.csv").read_bytes()

    calls_after_cold = list(mocked_api.rsps.calls)

    # Warm run to a different output dir using the same cache.
    warm_out = tmp_path / "warm_out"
    warm_out.mkdir()
    warm = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(warm_out),
        "--cache-dir",
        str(tmp_cache_dir),
    )
    assert warm.exit_code == 0, warm.stderr

    # New calls during the warm run.
    new_calls = [c for c in mocked_api.rsps.calls if c not in calls_after_cold]
    new_urls = [c.request.url for c in new_calls]
    for url in new_urls:
        assert "/suites" in url, (
            f"warm run hit non-discovery endpoint: {url}; full list: {new_urls}"
        )

    # runtimes.csv byte-identical between cold and warm runs.
    warm_runtimes = (warm_out / "runtimes.csv").read_bytes()
    assert cold_runtimes == warm_runtimes, (
        "runtimes.csv differs between cold and warm runs"
    )


def test_warm_run_default_out_without_network(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_cache_dir: Path,
    runner: Runner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario #24: warm run derives default --out from cached run timestamps."""

    monkeypatch.chdir(tmp_path)

    # Cold run with default --out (no flag).
    cold = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--cache-dir",
        str(tmp_cache_dir),
    )
    assert cold.exit_code == 0, cold.stderr
    folder_name = "2026-05-18T03-14-22Z_2026-05-20T17-22-09Z"
    cold_dir = tmp_path / folder_name
    assert cold_dir.exists()

    # Clear that directory; rerun warm. Folder name should reappear identically.
    import shutil

    shutil.rmtree(cold_dir)

    warm = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--cache-dir",
        str(tmp_cache_dir),
    )
    assert warm.exit_code == 0, warm.stderr
    assert cold_dir.exists(), (
        f"warm run should have recreated {cold_dir} from cached run ts"
    )


def test_no_cache_bypasses_reads_and_writes(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
    tmp_path: Path,
) -> None:
    """Scenario #25: --no-cache after a warm run still hits every endpoint."""

    # First run: populate cache.
    warmup = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )
    assert warmup.exit_code == 0, warmup.stderr

    # Snapshot the cache dir mtime + file set.
    files_before = {
        p: p.stat().st_mtime for p in tmp_cache_dir.rglob("*") if p.is_file()
    }

    # --no-cache run.
    no_cache_out = tmp_path / "no_cache_out"
    no_cache_out.mkdir()
    calls_before = len(mocked_api.rsps.calls)
    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(no_cache_out),
        "--cache-dir",
        str(tmp_cache_dir),
        "--no-cache",
    )
    assert result.exit_code == 0, result.stderr

    # Every endpoint should have been hit anew (no shortcut via cache).
    new_calls = mocked_api.rsps.calls[calls_before:]
    new_urls = [c.request.url for c in new_calls]
    assert any("/suites" in u for u in new_urls), f"no /suites call: {new_urls}"
    assert any("/runs" in u for u in new_urls), f"no /runs call: {new_urls}"
    assert any("/test_stats" in u for u in new_urls), f"no /test_stats call: {new_urls}"
    assert any("/summary.json" in u for u in new_urls), (
        f"no /summary.json call: {new_urls}"
    )

    # Cache directory untouched.
    files_after = {
        p: p.stat().st_mtime for p in tmp_cache_dir.rglob("*") if p.is_file()
    }
    assert files_before == files_after, (
        "cache files mutated under --no-cache; "
        f"diff: {set(files_before) ^ set(files_after)}"
    )


def test_different_windows_share_runs_cache(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_cache_dir: Path,
    tmp_path: Path,
    runner: Runner,
) -> None:
    """Scenario #28: different date windows reuse a single runs cache file.

    `/runs` only honours `suite_hash`, so the wire payload is identical
    regardless of the requested window. The second invocation must serve
    the runs list from cache without re-hitting `/runs`.
    """

    out_one = tmp_path / "out_one"
    out_one.mkdir()
    first = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(out_one),
        "--cache-dir",
        str(tmp_cache_dir),
        "--start-date",
        "2026-05-18",
        "--end-date",
        "2026-05-19",
    )
    assert first.exit_code == 0, first.stderr

    calls_after_first = list(mocked_api.rsps.calls)

    out_two = tmp_path / "out_two"
    out_two.mkdir()
    second = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(out_two),
        "--cache-dir",
        str(tmp_cache_dir),
        "--start-date",
        "2026-05-19",
        "--end-date",
        "2026-05-20",
    )
    assert second.exit_code == 0, second.stderr

    cache_file = tmp_cache_dir / CANONICAL_SUITE_HASH / "runs.json"
    assert cache_file.is_file(), f"expected a single runs cache file at {cache_file}"

    new_calls = mocked_api.rsps.calls[len(calls_after_first) :]
    assert not any("/runs" in c.request.url for c in new_calls), (
        f"second window must reuse cached /runs response; got "
        f"{[c.request.url for c in new_calls]}"
    )
