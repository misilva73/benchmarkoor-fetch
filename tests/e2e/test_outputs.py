"""End-to-end tests for per-artifact behaviour beyond the golden diff."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.e2e.conftest import (
    MockedApi,
    Runner,
)


def test_trace_parquet_is_derived_not_fetched(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #20: trace.parquet equals the projection of opcounts.json.

    Also asserts /summary.json is requested exactly once per suite.
    """

    import pandas as pd

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

    summary_calls = mocked_api.calls_to("/summary.json")
    assert len(summary_calls) == 1, (
        f"expected exactly 1 call to /summary.json; got {len(summary_calls)}: "
        f"{[c.request.url for c in summary_calls]}"
    )

    opcounts = json.loads((tmp_out_dir / "opcounts.json").read_text())
    trace_df = pd.read_parquet(tmp_out_dir / "trace.parquet")

    # The trace.parquet row count should equal the number of unique titles
    # captured in opcounts.json.
    assert len(trace_df) == len(opcounts), (
        f"trace.parquet rows ({len(trace_df)}) should equal opcounts.json "
        f"keys ({len(opcounts)})"
    )

    # Each row in trace_df corresponds to one fixture_name in opcounts.json,
    # and every opcode count should round-trip.
    title_col = "test_title" if "test_title" in trace_df.columns else "fixture_name"
    for _, row in trace_df.iterrows():
        title = row[title_col]
        assert title in opcounts, (
            f"trace.parquet title {title} missing from opcounts.json"
        )
        for op, count in opcounts[title].items():
            if op == "opcount":
                continue
            if op in trace_df.columns:
                assert row[op] == count, (
                    f"{title}/{op}: trace.parquet={row[op]} but opcounts.json={count}"
                )


def test_meta_dynamic_fields(
    mocked_api: MockedApi,
    canonical_config_path: Path,
    tmp_out_dir: Path,
    tmp_cache_dir: Path,
    runner: Runner,
) -> None:
    """Scenario #21: meta.json fetched_at / package_version / data_window."""

    import benchmarkoor_fetch

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

    meta = json.loads((tmp_out_dir / "meta.json").read_text())

    # fetched_at present and ISO-8601 (loose check).
    assert "fetched_at" in meta
    fetched_at = meta["fetched_at"]
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$",
        fetched_at,
    ), f"fetched_at not ISO-8601-ish: {fetched_at!r}"

    # package_version matches the installed package.
    assert "package_version" in meta
    expected_version = getattr(benchmarkoor_fetch, "__version__", None)
    if expected_version is not None:
        assert meta["package_version"] == expected_version, (
            f"package_version mismatch: meta says {meta['package_version']}, "
            f"package says {expected_version}"
        )

    # data_window — actual earliest/latest run ts from canonical fixture.
    assert "data_window" in meta
    data_window = meta["data_window"]
    assert data_window["start"] == "2026-05-18T03:14:22Z", (
        f"data_window.start should be earliest run start_ts, got "
        f"{data_window['start']!r}"
    )
    assert data_window["end"] == "2026-05-20T17:22:09Z", (
        f"data_window.end should be latest run start_ts, got {data_window['end']!r}"
    )

    # unparsed_fixtures reflects the run (zero on canonical fixtures).
    assert "unparsed_fixtures" in meta
    assert meta["unparsed_fixtures"] == [] or meta["unparsed_fixtures"] == {}, (
        f"canonical fixture has no unparseable titles; got {meta['unparsed_fixtures']}"
    )
