"""End-to-end tests for `benchmarkoor-fetch run`."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.e2e.conftest import (
    BENCHMARKOOR_BASE_URL,
    CANONICAL_RUN_IDS,
    MockedApi,
    Runner,
    register_runs,
    register_suites,
    register_summary,
    register_test_stats,
    variant_path,
)

# ---------------------------------------------------------------------------
# Scenario #1 — Happy path
# ---------------------------------------------------------------------------


def test_run_happy_path(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
    golden,
) -> None:
    """Scenario #1: happy path — all five artifacts present and diff clean."""

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

    assert (tmp_out_dir / "runtimes.csv").exists()
    assert (tmp_out_dir / "opcounts.json").exists()
    assert (tmp_out_dir / "bench_data.parquet").exists()
    assert (tmp_out_dir / "trace.parquet").exists()
    assert (tmp_out_dir / "meta.json").exists()

    golden.assert_csv(tmp_out_dir / "runtimes.csv", "runtimes.csv")
    golden.assert_json(tmp_out_dir / "opcounts.json", "opcounts.json")
    golden.assert_parquet(tmp_out_dir / "bench_data.parquet", "bench_data.parquet")
    golden.assert_parquet(tmp_out_dir / "trace.parquet", "trace.parquet")
    golden.assert_meta(tmp_out_dir / "meta.json", "meta.json")


# ---------------------------------------------------------------------------
# Scenario #2 — Default --out from data window
# ---------------------------------------------------------------------------


def test_run_default_out_folder(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_path: Path,
    tmp_cache_dir: Path,
    runner: Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario #2: default --out derives from earliest/latest run_ts."""

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--cache-dir",
        str(tmp_cache_dir),
    )

    assert result.exit_code == 0, result.stderr

    # The canonical fixture's earliest run_ts is 2026-05-18T03:14:22Z and
    # latest is 2026-05-20T17:22:09Z. Folder format: YYYY-MM-DDTHH-MM-SSZ.
    expected = tmp_path / "2026-05-18T03-14-22Z_2026-05-20T17-22-09Z"
    assert expected.exists() and expected.is_dir(), (
        f"expected default folder {expected} to exist; "
        f"cwd contents: {sorted(p.name for p in tmp_path.iterdir())}"
    )
    assert (expected / "meta.json").exists()


# ---------------------------------------------------------------------------
# Scenario #3 — CLI override wins over YAML
# ---------------------------------------------------------------------------


def test_run_cli_override_fork(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #3: --fork osaka beats YAML's amsterdam."""

    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
        "--fork",
        "osaka",
    )

    assert result.exit_code == 0, result.stderr

    # Fork filtering is client-side (server only honours discovery_path), so
    # we verify the override took effect via meta.json instead of the wire.
    meta = json.loads((tmp_out_dir / "meta.json").read_text())
    assert meta["query"]["fork"] == "osaka"
    # And the resolved suite_hash matches the osaka entry from the fixture.
    assert any(s["suite_hash"].startswith("0xosaka") for s in meta["suites"]), (
        f"expected osaka suite in meta.suites; got {meta['suites']}"
    )


# ---------------------------------------------------------------------------
# Scenario #4 — --token overrides BENCHMARKOOR_TOKEN
# ---------------------------------------------------------------------------


def test_run_token_flag_overrides_env(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #4: --token X overrides the BENCHMARKOOR_TOKEN env var."""

    override_token = "cli-override-token"
    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
        "--token",
        override_token,
    )

    assert result.exit_code == 0, result.stderr

    # Every recorded call should carry the override token, not the env one.
    assert mocked_api.rsps.calls, "no HTTP calls recorded"
    for call in mocked_api.rsps.calls:
        auth = call.request.headers.get("Authorization", "")
        assert auth == f"Bearer {override_token}", (
            f"expected Bearer {override_token}, got {auth!r}"
        )


# ---------------------------------------------------------------------------
# Scenario #6 — Output-flag combinations (parametrized)
# ---------------------------------------------------------------------------


_ARTIFACTS_ALL = (
    "runtimes.csv",
    "opcounts.json",
    "bench_data.parquet",
    "trace.parquet",
)


@pytest.mark.parametrize(
    ("flags", "expected_missing"),
    [
        (("--no-estimator-inputs",), ("runtimes.csv", "opcounts.json")),
        (("--no-merged-parquet",), ("bench_data.parquet",)),
        (("--no-trace-parquet",), ("trace.parquet",)),
        (
            (
                "--no-estimator-inputs",
                "--no-merged-parquet",
                "--no-trace-parquet",
            ),
            _ARTIFACTS_ALL,
        ),
    ],
    ids=[
        "no_estimator_inputs",
        "no_merged_parquet",
        "no_trace_parquet",
        "all_outputs_disabled",
    ],
)
def test_run_output_flag_combinations(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
    flags: tuple[str, ...],
    expected_missing: tuple[str, ...],
) -> None:
    """Scenario #6: disabled outputs are absent; meta.json always present."""

    result = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
        *flags,
    )

    assert result.exit_code == 0, result.stderr
    assert (tmp_out_dir / "meta.json").exists(), "meta.json must always be written"

    for name in _ARTIFACTS_ALL:
        produced = tmp_out_dir / name
        if name in expected_missing:
            assert not produced.exists(), f"{name} should be absent when flags={flags}"
        else:
            assert produced.exists(), f"{name} should be present when flags={flags}"


# ---------------------------------------------------------------------------
# Scenario #8 — --verbose emits `miss:` on cold run, silent on warm
# ---------------------------------------------------------------------------


def test_run_verbose_miss_then_silent(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #8: verbose cold run prints `miss:`; warm run is silent."""

    cold = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
        "--verbose",
    )
    assert cold.exit_code == 0, cold.stderr
    assert "miss:" in cold.stderr, (
        f"expected `miss:` in cold-run stderr; got: {cold.stderr!r}"
    )

    warm = runner.invoke(
        "run",
        "--config",
        str(canonical_config_path),
        "--out",
        str(tmp_out_dir),
        "--cache-dir",
        str(tmp_cache_dir),
        "--verbose",
    )
    assert warm.exit_code == 0, warm.stderr
    assert "miss:" not in warm.stderr, (
        f"warm-run stderr should not mention `miss:`; got: {warm.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Scenario #9 — unparsed titles warn but do not fail
# ---------------------------------------------------------------------------


def test_run_unparsed_titles_warn(
    mocked_api_raw: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #9: unparsed titles emit a warning + show up in meta.json."""

    register_suites(
        mocked_api_raw.rsps,
        body=json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "data"
                / "e2e"
                / "responses"
                / "suites.json"
            ).read_text()
        ),
    )
    register_runs(
        mocked_api_raw.rsps,
        body=json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "data"
                / "e2e"
                / "responses"
                / "runs.json"
            ).read_text()
        ),
    )
    register_test_stats(
        mocked_api_raw.rsps,
        body=json.loads(variant_path("unparsed_titles.json").read_text()),
    )
    register_summary(
        mocked_api_raw.rsps,
        suite_hash="0xabc1230000000000000000000000000000000000000000000000000000000000",
        body={
            (
                "tests/benchmarks/test_arithmetic.py::test_arithmetic"
                "[fork_Prague-add_uncached-bench_30000000_gas]"
            ): {
                "ADD": 1,
                "PUSH1": 2,
                "POP": 1,
                "STATICCALL": 0,
            },
            "totally_unparseable_title_no_brackets": {"ADD": 0, "STATICCALL": 0},
            "another!@#$%^&unparseable": {"ADD": 0, "STATICCALL": 0},
        },
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

    assert result.exit_code == 0, result.stderr
    assert re.search(r"WARN:\s+\d+\s+unparsed fixtures", result.stderr), (
        f"expected `WARN: N unparsed fixtures` in stderr; got: {result.stderr!r}"
    )

    meta = json.loads((tmp_out_dir / "meta.json").read_text())
    assert "unparsed_fixtures" in meta
    unparsed = meta["unparsed_fixtures"]
    assert "totally_unparseable_title_no_brackets" in unparsed
    assert "another!@#$%^&unparseable" in unparsed


# ---------------------------------------------------------------------------
# Scenario #9a — unparsed warning truncates at 10
# ---------------------------------------------------------------------------


def test_run_unparsed_truncated_at_ten(
    mocked_api_raw: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #9a: with 15 unparsed titles, stderr names 10 + total count."""

    register_suites(
        mocked_api_raw.rsps,
        body=json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "data"
                / "e2e"
                / "responses"
                / "suites.json"
            ).read_text()
        ),
    )
    register_runs(
        mocked_api_raw.rsps,
        body=json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "data"
                / "e2e"
                / "responses"
                / "runs.json"
            ).read_text()
        ),
    )
    register_test_stats(
        mocked_api_raw.rsps,
        body=json.loads(variant_path("unparsed_titles_15.json").read_text()),
    )
    register_summary(
        mocked_api_raw.rsps,
        suite_hash="0xabc1230000000000000000000000000000000000000000000000000000000000",
        body={
            f"unparseable_title_{n:02d}": {"ADD": 0, "STATICCALL": 0}
            for n in range(1, 16)
        },
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

    assert result.exit_code == 0, result.stderr

    # Locate the warning line.
    warn_lines = [ln for ln in result.stderr.splitlines() if "unparsed fixtures" in ln]
    assert warn_lines, f"no `unparsed fixtures` line in stderr: {result.stderr!r}"
    line = warn_lines[0]
    # Total count must be 15.
    assert "15" in line, f"warning line should reference 15: {line!r}"
    # Must end with an ellipsis or `, …` indicating truncation.
    assert "…" in line or "..." in line, (
        f"warning line should indicate truncation: {line!r}"
    )
    # Count the listed names — should be exactly 10.
    listed = re.findall(r"unparseable_title_\d{2}", line)
    assert len(listed) == 10, (
        f"expected 10 names in the warning line, got {len(listed)}: {line!r}"
    )

    # All 15 land in meta.json.
    meta = json.loads((tmp_out_dir / "meta.json").read_text())
    assert "unparsed_fixtures" in meta
    assert len(meta["unparsed_fixtures"]) == 15


# ---------------------------------------------------------------------------
# Scenario #9b — one run_id returns zero test_stats rows
# ---------------------------------------------------------------------------


def test_run_one_empty_run_id(
    mocked_api_raw: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #9b: one of three runs paginates to total=0; pipeline completes."""

    base_responses = Path(__file__).resolve().parents[1] / "data" / "e2e" / "responses"
    register_suites(
        mocked_api_raw.rsps,
        body=json.loads((base_responses / "suites.json").read_text()),
    )
    register_runs(
        mocked_api_raw.rsps,
        body=json.loads((base_responses / "runs.json").read_text()),
    )

    # Per-run_id stats: run-001 has one row, run-002 has zero, run-003 has two.
    import responses as _r

    variants = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "e2e"
        / "variants"
        / "test_stats_one_empty"
    )
    for run_id, body_path in [
        ("run-001-full", variants / "run_001_page1.json"),
        ("run-002-full", variants / "run_002_empty.json"),
        ("run-003-full", variants / "run_003_page1.json"),
    ]:
        mocked_api_raw.rsps.add(
            _r.GET,
            f"{BENCHMARKOOR_BASE_URL}/test_stats",
            json=json.loads(body_path.read_text()),
            status=200,
            match=[
                _r.matchers.query_param_matcher(
                    {"run_id": f"eq.{run_id}"}, strict_match=False
                )
            ],
        )

    register_summary(
        mocked_api_raw.rsps,
        suite_hash="0xabc1230000000000000000000000000000000000000000000000000000000000",
        body={
            "tests": [
                {
                    "name": (
                        "tests/benchmarks/test_arithmetic.py::test_arithmetic"
                        "[fork_Prague-add_uncached-bench_30000000_gas].txt"
                    ),
                    "opcode_count": {
                        "ADD": 1,
                        "PUSH1": 2,
                        "POP": 1,
                        "STATICCALL": 0,
                    },
                }
            ]
        },
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

    assert result.exit_code == 0, result.stderr

    # bench_data.parquet should contain only the rows from runs 001 and 003.
    import pandas as pd

    bench = pd.read_parquet(tmp_out_dir / "bench_data.parquet")
    # run-001 has 1 row, run-003 has 2 rows. Total 3.
    assert len(bench) == 3, f"expected 3 rows, got {len(bench)}: {bench}"
    assert set(bench["run_id"].unique()) == {"run-001-full", "run-003-full"}, (
        f"run-002-full should have produced no rows; got {bench['run_id'].unique()}"
    )

    meta = json.loads((tmp_out_dir / "meta.json").read_text())
    assert "row_counts" in meta
    # row_counts should reflect the post-filter count.
    assert (
        meta["row_counts"].get("bench_data") == 3
        or meta["row_counts"].get("bench_data.parquet") == 3
    ), f"row_counts does not reflect filtered count: {meta['row_counts']}"

    # Sanity: the run_ids we asked about — verify all three /test_stats
    # calls happened. PostgREST `eq.` prefix wraps each filter value.
    for run_id in CANONICAL_RUN_IDS:
        assert any(
            f"run_id=eq.{run_id}" in c.request.url
            for c in mocked_api_raw.calls_to("/test_stats")
        ), f"expected a /test_stats call for {run_id}"
