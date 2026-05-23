"""End-to-end tests for the `BenchmarkoorClient` library API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.conftest import (
    CANONICAL_SUITE_HASH,
    MockedApi,
    register_runs,
    register_summary,
    register_test_stats,
)


def test_style_a_run_returns_fetch_result(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_cache_dir: Path,
    bench_token: str,
) -> None:
    """Scenario #12: Style A — `client.run(config)` returns FetchResult."""

    import pandas as pd

    from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig

    config = FetchConfig.from_yaml(canonical_config_path)
    config = config.with_cli_overrides(cache_dir=tmp_cache_dir)
    client = BenchmarkoorClient(token=bench_token)

    result = client.run(config)

    assert isinstance(result.bench_df, pd.DataFrame)
    assert isinstance(result.trace_df, pd.DataFrame)

    expected_bench_columns = {
        "run_id",
        "client_name",
        "test_title",
        "test_file",
        "test_name",
        "test_opcode",
        "test_params",
        "test_runtime_ms",
        "ingestion_timestamp",
        "block_limit_million",
        "opcount",
    }
    assert expected_bench_columns.issubset(set(result.bench_df.columns)), (
        f"bench_df missing columns: "
        f"{expected_bench_columns - set(result.bench_df.columns)}"
    )

    # trace_df: test_title, opcount, then opcode columns.
    assert "test_title" in result.trace_df.columns
    assert "opcount" in result.trace_df.columns

    # 5 unique titles in the canonical fixture * 3 runs * 2 clients = 30 rows.
    assert len(result.bench_df) == 30, f"expected 30 rows, got {len(result.bench_df)}"


def test_fetch_result_write_matches_cli(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_path: Path,
    tmp_cache_dir: Path,
    bench_token: str,
    golden,
) -> None:
    """Scenario #13: FetchResult.write() produces the same bundle as the CLI."""

    from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig

    config = FetchConfig.from_yaml(canonical_config_path)
    config = config.with_cli_overrides(cache_dir=tmp_cache_dir)
    client = BenchmarkoorClient(token=bench_token)
    result = client.run(config)

    out_dir = tmp_path / "library_out"
    out_dir.mkdir()
    result.write(out_dir)

    golden.assert_csv(out_dir / "runtimes.csv", "runtimes.csv")
    golden.assert_json(out_dir / "opcounts.json", "opcounts.json")
    golden.assert_parquet(out_dir / "bench_data.parquet", "bench_data.parquet")
    golden.assert_parquet(out_dir / "trace.parquet", "trace.parquet")
    golden.assert_meta(out_dir / "meta.json", "meta.json")


def test_style_b_granular(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_cache_dir: Path,
    bench_token: str,
) -> None:
    """Scenario #14: Style B — resolve → list_runs → fetch → parse."""

    import pandas as pd

    from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig

    client = BenchmarkoorClient(token=bench_token, cache_dir=tmp_cache_dir)

    suite_hash = client.resolve_suite(
        network="jochemnet",
        fork="amsterdam",
        test_type="compute",
    )
    assert suite_hash == CANONICAL_SUITE_HASH

    run_ids = client.list_runs(
        suite_hash,
        start_date="2026-05-18",
        end_date="2026-05-20",
        run_type="full",
    )
    assert isinstance(run_ids, (list, tuple, pd.DataFrame))

    raw_df = client.fetch_test_stats(run_ids)
    assert isinstance(raw_df, pd.DataFrame)
    # Wire field is run_duration_ms; per §4 it gets renamed at parse time.
    # At this granular layer the column should already be the renamed one.
    assert "test_runtime_ms" in raw_df.columns

    trace_df_raw = client.fetch_trace(suite_hash)

    bench_df, trace_df = client.parse(raw_df, trace_df_raw)
    assert "test_opcode" in bench_df.columns
    assert "opcount" in bench_df.columns

    # Style B's bench_df should equal Style A's output for the same inputs.
    config = FetchConfig.from_yaml(canonical_config_path).with_cli_overrides(
        cache_dir=tmp_cache_dir
    )
    style_a = client.run(config)

    # Sort both for order-independent comparison.
    def sort_key(df: pd.DataFrame) -> pd.DataFrame:
        return df.sort_values(by=list(df.columns)).reset_index(drop=True)

    pd.testing.assert_frame_equal(sort_key(bench_df), sort_key(style_a.bench_df))


def test_constructor_token_beats_env(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario #15: constructor token wins over BENCHMARKOOR_TOKEN env var."""

    monkeypatch.setenv("BENCHMARKOOR_TOKEN", "env-token")
    from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig

    constructor_token = "constructor-token"
    config = FetchConfig.from_yaml(canonical_config_path).with_cli_overrides(
        cache_dir=tmp_cache_dir
    )
    client = BenchmarkoorClient(token=constructor_token)
    client.run(config)

    assert mocked_api.rsps.calls, "no HTTP calls recorded"
    for call in mocked_api.rsps.calls:
        auth = call.request.headers.get("Authorization", "")
        assert auth == f"Bearer {constructor_token}", (
            f"expected Bearer {constructor_token}, got {auth!r}"
        )


def test_explicit_suites_skips_discovery(
    mocked_api_raw: MockedApi,
    tmp_path: Path,
    tmp_cache_dir: Path,
    bench_token: str,
) -> None:
    """Scenario #16: query.suites explicit list skips /suites discovery."""

    hash_one = CANONICAL_SUITE_HASH
    hash_two = "0xdef456" + "0" * 58

    # Register runs + test_stats + summary for both suites.
    base_responses = Path(__file__).resolve().parents[1] / "data" / "e2e" / "responses"
    register_runs(
        mocked_api_raw.rsps,
        body=json.loads((base_responses / "runs.json").read_text()),
    )
    register_test_stats(
        mocked_api_raw.rsps,
        body=json.loads((base_responses / "test_stats_page1.json").read_text()),
    )
    register_summary(
        mocked_api_raw.rsps,
        suite_hash=hash_one,
        body=json.loads((base_responses / "summary.json").read_text()),
    )
    register_summary(
        mocked_api_raw.rsps,
        suite_hash=hash_two,
        body=json.loads((base_responses / "summary.json").read_text()),
    )
    # Note: deliberately no /suites registration — a call there would error.

    yaml_path = tmp_path / "fetch.yaml"
    yaml_path.write_text(
        "query:\n"
        "  network: jochemnet\n"
        "  fork: amsterdam\n"
        "  test_type: compute\n"
        f"  suites:\n    - {hash_one}\n    - {hash_two}\n"
        "http:\n  page_size: 100\n  max_workers: 5\n  retries: 3\n"
        "  backoff_factor: 2\n  retry_status: [502, 503, 524]\n"
        "output:\n  estimator_inputs: true\n  merged_parquet: true\n"
        "  trace_parquet: true\n"
        f"cache:\n  enabled: true\n  dir: {tmp_cache_dir}\n"
    )

    from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig

    config = FetchConfig.from_yaml(yaml_path)
    client = BenchmarkoorClient(token=bench_token)
    result = client.run(config)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result.write(out_dir)

    assert mocked_api_raw.call_count("/suites") == 0, (
        f"expected zero /suites calls; got "
        f"{[c.request.url for c in mocked_api_raw.calls_to('/suites')]}"
    )

    meta = json.loads((out_dir / "meta.json").read_text())
    suites_meta = meta.get("suites", [])
    # `suites` may be a list of dicts or a list of hashes; accept either shape.
    hashes_in_meta: set[str] = set()
    for entry in suites_meta:
        if isinstance(entry, str):
            hashes_in_meta.add(entry)
        elif isinstance(entry, dict):
            if "suite_hash" in entry:
                hashes_in_meta.add(entry["suite_hash"])
    assert hash_one in hashes_in_meta and hash_two in hashes_in_meta, (
        f"both hashes should be in meta.json.suites; got {suites_meta}"
    )
