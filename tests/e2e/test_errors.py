"""End-to-end tests for error paths and exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses as _r

from tests.e2e.conftest import (
    BENCHMARKOOR_BASE_URL,
    CANONICAL_SUITE_HASH,
    MockedApi,
    Runner,
    load_variant,
    register_runs,
    register_suites,
    register_summary,
    variant_path,
)

# ---------------------------------------------------------------------------
# Scenario #31 — missing token
# ---------------------------------------------------------------------------


def test_missing_token_exits_1(
    no_token: None,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #31: no --token and no BENCHMARKOOR_TOKEN env → exit 1."""

    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )

    assert result.exit_code == 1, (
        f"expected exit 1, got {result.exit_code}; stderr: {result.stderr!r}"
    )
    assert "BENCHMARKOOR_TOKEN" in result.stderr, (
        f"stderr should mention BENCHMARKOOR_TOKEN: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Scenario #32 — invalid YAML (missing `fork`)
# ---------------------------------------------------------------------------


def test_invalid_yaml_missing_fork_exits_1(
    bench_token: str,
    tmp_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #32: missing required `fork` → exit 1; stderr names the field."""

    bad_yaml = tmp_path / "bad_fetch.yaml"
    bad_yaml.write_text(
        "query:\n"
        "  network: jochemnet\n"
        "  test_type: compute\n"
        "http:\n  page_size: 100\n"
        "output:\n  estimator_inputs: true\n"
        "cache:\n  enabled: false\n"
    )

    result = runner.invoke(
        "run",
        "--config",
        str(bad_yaml),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )

    assert result.exit_code == 1, (
        f"expected exit 1, got {result.exit_code}; stderr: {result.stderr!r}"
    )
    assert "fork" in result.stderr, (
        f"stderr should name the missing `fork` field: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Scenario #33 — /suites returns 401
# ---------------------------------------------------------------------------


def test_suites_401_exits_2(
    mocked_api_raw: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #33: /suites 401 → exit 2; stderr signals auth failure."""

    mocked_api_raw.rsps.add(
        _r.GET,
        f"{BENCHMARKOOR_BASE_URL}/suites",
        json=load_variant("auth_401.json"),
        status=401,
    )

    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )

    assert result.exit_code == 2, (
        f"expected exit 2, got {result.exit_code}; stderr: {result.stderr!r}"
    )
    lower_err = result.stderr.lower()
    assert (
        "401" in result.stderr
        or "auth" in lower_err
        or "unauthorized" in lower_err
        or "unauthorised" in lower_err
    ), f"stderr should distinguish auth failure: {result.stderr!r}"


# ---------------------------------------------------------------------------
# Scenarios #34 + #35 — /test_stats retry behaviour (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("num_502", "expected_exit", "scenario_id"),
    [
        (4, 2, "exhausted_retries"),
        (2, 0, "retry_then_succeed"),
    ],
)
def test_test_stats_retry_behaviour(
    mocked_api_raw: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
    num_502: int,
    expected_exit: int,
    scenario_id: str,
) -> None:
    """Scenarios #34/#35: /test_stats retry budget — exhausted vs retry-then-200."""

    base_responses = Path(__file__).resolve().parents[1] / "data" / "e2e" / "responses"
    register_suites(
        mocked_api_raw.rsps,
        body=json.loads((base_responses / "suites.json").read_text()),
    )
    register_runs(
        mocked_api_raw.rsps,
        body=json.loads((base_responses / "runs.json").read_text()),
    )

    for _ in range(num_502):
        mocked_api_raw.rsps.add(
            _r.GET,
            f"{BENCHMARKOOR_BASE_URL}/test_stats",
            json={"error": "bad gateway"},
            status=502,
        )
    if expected_exit == 0:
        # Append a 200 after the 502s — small valid page.
        mocked_api_raw.rsps.add(
            _r.GET,
            f"{BENCHMARKOOR_BASE_URL}/test_stats",
            json=json.loads(
                (
                    variant_path("test_stats_502_then_200") / "response_3_200.json"
                ).read_text()
            ),
            status=200,
        )
        register_summary(
            mocked_api_raw.rsps,
            suite_hash=CANONICAL_SUITE_HASH,
            body=json.loads((base_responses / "summary.json").read_text()),
        )

    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
        "--no-cache",
    )

    assert result.exit_code == expected_exit, (
        f"{scenario_id}: expected exit {expected_exit}, got "
        f"{result.exit_code}; stderr: {result.stderr!r}"
    )

    if expected_exit == 2:
        # Stderr should mention retries.
        lower_err = result.stderr.lower()
        mentions_retry = (
            "retry" in lower_err or "retries" in lower_err or "502" in result.stderr
        )
        assert mentions_retry, (
            f"stderr should reference retry attempts: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Scenario #36 — /runs returns empty
# ---------------------------------------------------------------------------


def test_runs_empty_exits_3(
    mocked_api_raw: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #36: zero runs in window → exit 3, no outputs written."""

    base_responses = Path(__file__).resolve().parents[1] / "data" / "e2e" / "responses"
    register_suites(
        mocked_api_raw.rsps,
        body=json.loads((base_responses / "suites.json").read_text()),
    )
    register_runs(mocked_api_raw.rsps, body=load_variant("runs_empty.json"))

    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )

    assert result.exit_code == 3, (
        f"expected exit 3, got {result.exit_code}; stderr: {result.stderr!r}"
    )
    assert "no runs" in result.stderr.lower() or "empty" in result.stderr.lower(), (
        f"stderr should mention no runs matched window: {result.stderr!r}"
    )

    # No output files written.
    assert not (tmp_out_dir / "runtimes.csv").exists()
    assert not (tmp_out_dir / "opcounts.json").exists()
    assert not (tmp_out_dir / "bench_data.parquet").exists()
    assert not (tmp_out_dir / "trace.parquet").exists()


# ---------------------------------------------------------------------------
# Scenario #37 — Unknown suite_hash in explicit query.suites → 404 on /runs
# ---------------------------------------------------------------------------


def test_explicit_suite_404_on_runs_exits_2(
    mocked_api_raw: MockedApi,
    tmp_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    bench_token: str,
    runner: Runner,
) -> None:
    """Scenario #37: explicit suite_hash with 404 on /runs → exit 2."""

    unknown_hash = "0xunknown" + "0" * 56
    yaml_path = tmp_path / "explicit_suites.yaml"
    yaml_path.write_text(
        "query:\n"
        "  network: jochemnet\n"
        "  fork: amsterdam\n"
        "  test_type: compute\n"
        f"  suites:\n    - {unknown_hash}\n"
        "http:\n  page_size: 100\n  max_workers: 5\n  retries: 3\n"
        "  backoff_factor: 2\n  retry_status: [502, 503, 524]\n"
        "output:\n  estimator_inputs: true\n  merged_parquet: true\n"
        "  trace_parquet: true\n"
        "cache:\n  enabled: false\n  dir: ~/.cache/benchmarkoor-fetch\n"
    )

    mocked_api_raw.rsps.add(
        _r.GET,
        f"{BENCHMARKOOR_BASE_URL}/runs",
        json={"error": "not found", "message": f"unknown suite_hash {unknown_hash}"},
        status=404,
    )

    result = runner.invoke(
        "run",
        "--config",
        str(yaml_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
    )

    assert result.exit_code == 2, (
        f"expected exit 2, got {result.exit_code}; stderr: {result.stderr!r}"
    )
    assert unknown_hash in result.stderr, (
        f"stderr should name the offending hash: {result.stderr!r}"
    )
