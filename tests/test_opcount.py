"""Unit tests for `benchmarkoor_fetch.parse.opcount`.

Covers the four shapes the merge can take (regular opcode, precompile,
unknown opcode, missing opcode) plus fork-aware precompile resolution and
multi-row trace alignment.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from benchmarkoor_fetch.parse import opcount as opcount_module


def add_opcount(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Lazy lookup so collection succeeds before `parse.opcount` is implemented."""
    return opcount_module.add_opcount(*args, **kwargs)


DATA_DIR = Path(__file__).parent / "data" / "opcount"

REGULAR_TITLE = (
    "test_arithmetic.py__"
    "test_arithmetic[fork_Prague-benchmark_test-opcode_ADD--benchmark_30M]"
)
PRECOMPILE_TITLE = (
    "test_alt_bn128.py__"
    "test_alt_bn128_uncachable[fork_Prague-benchmark_test-ec_add-benchmark_30M]"
)


def _load_trace(name: str) -> pd.DataFrame:
    return pd.read_parquet(DATA_DIR / name)


# --------------------------------------------------------------------------- #
# Scenario #22: regular opcode lookup
# --------------------------------------------------------------------------- #


def test_regular_opcode_lookup() -> None:
    """Scenario #22: test_opcode=ADD, trace[ADD]=42 → opcount == 42."""
    bench = pd.DataFrame({"test_title": [REGULAR_TITLE], "test_opcode": ["ADD"]})
    trace = _load_trace("regular_opcode.parquet")
    out = add_opcount(bench, trace, fork="prague")
    assert int(out.iloc[0]["opcount"]) == 42


# --------------------------------------------------------------------------- #
# Scenario #23: precompile uses STATICCALL count
# --------------------------------------------------------------------------- #


def test_precompile_uses_staticcall_count() -> None:
    """Scenario #23: precompile opcode → opcount = trace[STATICCALL]."""
    bench = pd.DataFrame({"test_title": [PRECOMPILE_TITLE], "test_opcode": ["ECADD"]})
    trace = _load_trace("precompile_opcode.parquet")
    out = add_opcount(bench, trace, fork="prague")
    assert int(out.iloc[0]["opcount"]) == 7


# --------------------------------------------------------------------------- #
# Scenario #24: unknown opcode → 0 (or NaN per port behaviour)
# --------------------------------------------------------------------------- #


def test_unknown_opcode_resolves_to_zero_or_nan() -> None:
    """Scenario #24: unknown opcode → opcount matches the port's behaviour.

    The reference port emits literal 0 (`np.where(..., 0)`); some downstream
    consumers expected NaN. Accept either, but reject any other value.
    """
    title = "test_unrelated_thing.py__test_something_weird"
    bench = pd.DataFrame({"test_title": [title], "test_opcode": ["FOO"]})
    trace = _load_trace("unknown_opcode.parquet")
    out = add_opcount(bench, trace, fork="prague")
    value = out.iloc[0]["opcount"]
    assert value == 0 or pd.isna(value), (
        f"Unknown opcode must resolve to 0 or NaN per the port, got {value!r}."
    )


# --------------------------------------------------------------------------- #
# Scenario #25: missing test_opcode → NaN
# --------------------------------------------------------------------------- #


def test_missing_opcode_resolves_to_nan() -> None:
    """Scenario #25: empty test_opcode → opcount NaN (not 0)."""
    bench = pd.DataFrame({"test_title": [REGULAR_TITLE], "test_opcode": [""]})
    trace = _load_trace("regular_opcode.parquet")
    out = add_opcount(bench, trace, fork="prague")
    value = out.iloc[0]["opcount"]
    assert pd.isna(value), (
        "Empty test_opcode must resolve to NaN "
        f"(distinct from unknown→0), got {value!r}."
    )


# --------------------------------------------------------------------------- #
# Scenario #26: fork-aware precompile resolution
# --------------------------------------------------------------------------- #


def test_fork_aware_precompile_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario #26: add_opcount(..., fork='osaka') calls get_precompiles('osaka')."""
    calls: list[str] = []

    def fake_get_precompiles(fork: str) -> set[str]:
        calls.append(fork)
        return {"ECADD", "ECMUL"}

    monkeypatch.setattr(
        opcount_module, "get_precompiles", fake_get_precompiles, raising=False
    )

    bench = pd.DataFrame({"test_title": [PRECOMPILE_TITLE], "test_opcode": ["ECADD"]})
    trace = _load_trace("precompile_opcode.parquet")
    add_opcount(bench, trace, fork="osaka")

    assert "osaka" in calls, (
        "add_opcount must consult get_precompiles(fork) so the precompile "
        "table follows the configured fork."
    )


# --------------------------------------------------------------------------- #
# Scenario #27: multiple rows sharing the same test_title get the same opcount
# --------------------------------------------------------------------------- #


def test_trace_row_alignment_across_duplicate_titles() -> None:
    """Scenario #27: duplicate test_title rows resolve to identical opcount."""
    bench = pd.DataFrame(
        {
            "test_title": [REGULAR_TITLE, REGULAR_TITLE, REGULAR_TITLE],
            "test_opcode": ["ADD", "ADD", "ADD"],
        }
    )
    trace = _load_trace("regular_opcode.parquet")
    out = add_opcount(bench, trace, fork="prague")
    assert out["opcount"].nunique() == 1
    assert int(out.iloc[0]["opcount"]) == 42
