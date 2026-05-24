"""Unit tests for `benchmarkoor_fetch.parse.titles`.

Locks parser behaviour via the snapshot under `tests/data/`. Any intentional
parser change must update `sample_test_titles_expected.csv` in the same
commit.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from benchmarkoor_fetch import parse_test_titles

DATA_DIR = Path(__file__).parent / "data"
SAMPLE_TITLES = DATA_DIR / "sample_test_titles.txt"
EXPECTED_CSV = DATA_DIR / "sample_test_titles_expected.csv"


def _load_sample_titles() -> pd.DataFrame:
    """Load the raw sample titles into a 1-column DataFrame."""
    titles = [line for line in SAMPLE_TITLES.read_text().splitlines() if line]
    return pd.DataFrame({"test_title": titles})


# --------------------------------------------------------------------------- #
# Scenario #17: snapshot diff
# --------------------------------------------------------------------------- #


def test_snapshot_matches_expected_csv() -> None:
    """Scenario #17: parsed sample equals committed expected CSV row-by-row."""
    raw = _load_sample_titles()
    expected = pd.read_csv(EXPECTED_CSV, dtype=str, keep_default_na=False)

    result = parse_test_titles(raw)
    # parse_test_titles may return (df, unparsed); handle both shapes.
    parsed = result[0] if isinstance(result, tuple) else result

    parsed_str = parsed.astype(str).reset_index(drop=True)
    expected_str = expected.reset_index(drop=True)

    pd.testing.assert_frame_equal(
        parsed_str[expected_str.columns.tolist()],
        expected_str,
        check_like=False,
    )


# --------------------------------------------------------------------------- #
# Scenario #19: unparsed titles flow through with empty parsed columns
# --------------------------------------------------------------------------- #


def test_unparsed_title_flows_through_with_empty_columns() -> None:
    """Scenario #19: a no-match title still produces a row; parsed columns empty."""
    bogus = "some_freeform_title_that_does_not_match_anything"
    df = pd.DataFrame({"test_title": [bogus]})

    result = parse_test_titles(df)
    parsed = result[0] if isinstance(result, tuple) else result

    assert len(parsed) == 1
    row = parsed.iloc[0]
    for col in ("test_file", "test_name", "test_opcode", "test_params"):
        value = row.get(col)
        assert value is None or pd.isna(value) or value == ""
    blm = row.get("block_limit_million")
    assert blm is None or pd.isna(blm)


# --------------------------------------------------------------------------- #
# Scenario #20: parser returns unparsed titles alongside the DataFrame
# --------------------------------------------------------------------------- #


def test_parser_returns_unparsed_list_alongside_dataframe() -> None:
    """Scenario #20: parse_test_titles(df) -> (df, unparsed: list[str])."""
    df = pd.DataFrame(
        {
            "test_title": [
                "test_arithmetic.py__test_arithmetic[fork_Amsterdam-benchmark_test-opcode_ADD--benchmark_100M]",
                "some_freeform_title_that_does_not_match_anything",
            ]
        }
    )
    result = parse_test_titles(df)
    assert isinstance(result, tuple), (
        "parse_test_titles must return (DataFrame, list[str]) so the pipeline "
        "can emit a single end-of-run warning. The parser itself does not warn."
    )
    parsed, unparsed = result
    assert isinstance(parsed, pd.DataFrame)
    assert isinstance(unparsed, list)
    assert "some_freeform_title_that_does_not_match_anything" in unparsed


# --------------------------------------------------------------------------- #
# Scenario #21: idempotent on already-parsed input
# --------------------------------------------------------------------------- #


def test_parser_is_idempotent_on_already_parsed_input() -> None:
    """Scenario #21: parsing twice yields identical output (no in-place mutation)."""
    raw = _load_sample_titles()
    first = parse_test_titles(raw)
    first_df = first[0] if isinstance(first, tuple) else first
    second = parse_test_titles(first_df.copy())
    second_df = second[0] if isinstance(second, tuple) else second

    pd.testing.assert_frame_equal(
        first_df.reset_index(drop=True),
        second_df.reset_index(drop=True),
    )


# --------------------------------------------------------------------------- #
# Scenario #21a: block_limit_million extracted from EELS suffix
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "test_keccak.py__test_keccak_diff_mem_msg_sizes[fork_Amsterdam-benchmark_test-msg_size_0-mem_size_0-benchmark_300M]",
            300,
        ),
        (
            "test_keccak.py__test_keccak_diff_mem_msg_sizes[fork_Amsterdam-benchmark_test-msg_size_0-mem_size_0-benchmark_140M]",
            140,
        ),
        (
            "test_keccak.py__test_keccak_diff_mem_msg_sizes[fork_Amsterdam-benchmark_test-msg_size_0-mem_size_0-benchmark_100M]",
            100,
        ),
        (
            "test_arithmetic.py__test_arithmetic[fork_Amsterdam-benchmark_test-opcode_ADD--warm_300_runs]",
            None,
        ),
    ],
)
def test_block_limit_million_extraction(title: str, expected: int | None) -> None:
    """Scenario #21a: block_limit_million parsed from the `benchmark_<N>M` suffix."""
    df = pd.DataFrame({"test_title": [title]})
    result = parse_test_titles(df)
    parsed = result[0] if isinstance(result, tuple) else result

    actual = parsed.iloc[0]["block_limit_million"]
    if expected is None:
        assert actual is None or pd.isna(actual)
    else:
        assert int(actual) == expected
