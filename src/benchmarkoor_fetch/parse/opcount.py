from __future__ import annotations

import numpy as np
import pandas as pd

from benchmarkoor_fetch.parse.precompiles import get_precompiles  # noqa: F401


def _normalise_trace(trace_df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of trace_df with `test_title` as the index."""
    if trace_df.index.name == "test_title":
        return trace_df
    if "test_title" in trace_df.columns:
        return trace_df.set_index("test_title")
    return trace_df


def add_opcount(
    bench_df: pd.DataFrame,
    trace_df: pd.DataFrame,
    fork: str,
) -> pd.DataFrame:
    """Append an `opcount` column to bench_df by joining the trace data.

    For precompile opcodes (per `get_precompiles(fork)`), the count comes from
    the trace's STATICCALL column. For regular opcodes, it comes from the
    trace column matching `test_opcode`. Empty `test_opcode` yields NaN;
    opcodes missing from the trace yield 0.

    Args:
        bench_df: DataFrame with `test_title` and `test_opcode` columns.
        trace_df: DataFrame indexed by (or with column) `test_title`, with one
            column per opcode.
        fork: Fork name; passed to `get_precompiles` for the precompile set.

    Returns:
        A copy of bench_df with an additional `opcount` column.
    """
    # Resolve via module attribute so monkeypatching `get_precompiles` works.
    import benchmarkoor_fetch.parse.opcount as _self  # type: ignore[import-not-found]

    precompiles = _self.get_precompiles(fork)

    trace = _normalise_trace(trace_df)
    out = bench_df.copy()

    opcounts: list[float] = []
    for title, opcode in zip(out["test_title"], out["test_opcode"], strict=True):
        if not isinstance(opcode, str) or opcode == "":
            opcounts.append(float("nan"))
            continue
        if title not in trace.index:
            opcounts.append(0.0)
            continue
        row = trace.loc[title]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        lookup_col = "STATICCALL" if opcode in precompiles else opcode
        if lookup_col in row.index:
            value = row[lookup_col]
            opcounts.append(float(value) if pd.notna(value) else 0.0)
        else:
            opcounts.append(0.0)

    out["opcount"] = pd.array(opcounts, dtype="Float64")
    return out


__all__ = ["add_opcount", "get_precompiles"]


_ = np  # keep the import to mirror the reference implementation's stack
