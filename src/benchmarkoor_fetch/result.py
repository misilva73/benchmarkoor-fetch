from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class FetchResult:
    """In-memory result of a pipeline run.

    Holds the parsed bench DataFrame, the derived trace DataFrame, and the
    opcount mapping needed to materialise the artifact bundle on disk
    (`runtimes.csv`, `opcounts.json`, `bench_data.parquet`, `trace.parquet`,
    `meta.json`).

    The `output_flags` dict gates which artifacts `write()` produces;
    `meta.json` is always written.
    """

    bench_df: pd.DataFrame
    trace_df: pd.DataFrame
    opcounts: dict[str, dict[str, float]]
    meta: dict[str, Any]
    output_flags: dict[str, bool] = field(
        default_factory=lambda: {
            "estimator_inputs": True,
            "merged_parquet": True,
            "trace_parquet": True,
        }
    )

    def default_out_dirname(self) -> str:
        """Return `{earliest_run_ts}_{latest_run_ts}` formatted YYYY-MM-DDTHH-MM-SSZ."""
        window = self.meta.get("data_window") or {}
        start = _format_ts(window.get("start"))
        end = _format_ts(window.get("end"))
        return f"{start}_{end}"

    def write(self, out_dir: Path) -> None:
        """Write the artifact bundle to `out_dir` (created if missing)."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        flags = self.output_flags
        row_counts: dict[str, int] = {"bench_data": int(len(self.bench_df))}

        if flags.get("estimator_inputs", True):
            runtimes = _runtimes_frame(self.bench_df)
            runtimes.to_csv(out_dir / "runtimes.csv", index=False)
            (out_dir / "opcounts.json").write_text(
                json.dumps(self.opcounts, indent=2, sort_keys=True)
            )
            row_counts["runtimes"] = int(len(runtimes))
            row_counts["opcounts"] = int(len(self.opcounts))

        if flags.get("merged_parquet", True):
            self.bench_df.to_parquet(out_dir / "bench_data.parquet", index=False)

        if flags.get("trace_parquet", True):
            self.trace_df.to_parquet(out_dir / "trace.parquet", index=False)
            row_counts["trace"] = int(len(self.trace_df))

        meta = dict(self.meta)
        meta["row_counts"] = row_counts
        (out_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True, default=str)
        )


def _runtimes_frame(bench_df: pd.DataFrame) -> pd.DataFrame:
    """Project bench_df into the `runtimes.csv` schema."""
    if bench_df.empty:
        return pd.DataFrame(columns=["client_name", "fixture_name", "test_runtime_ms"])
    return pd.DataFrame(
        {
            "client_name": bench_df["client_name"],
            "fixture_name": bench_df["test_title"],
            "test_runtime_ms": bench_df["test_runtime_ms"],
        }
    )


def _format_ts(value: Any) -> str:
    """Convert an ISO timestamp into a filesystem-safe form (YYYY-MM-DDTHH-MM-SSZ)."""
    if not isinstance(value, str) or not value:
        return "unknown"
    return value.replace(":", "-")
