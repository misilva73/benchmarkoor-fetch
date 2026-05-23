from __future__ import annotations

import re
import sys
from importlib.resources import files
from pathlib import Path

import pandas as pd


def _load_opcodes() -> frozenset[str]:
    text = (
        files("benchmarkoor_fetch.parse.data")
        .joinpath("opcodes_in_test_name.txt")
        .read_text()
    )
    return frozenset(line.strip() for line in text.splitlines() if line.strip())


_OPCODES: frozenset[str] = _load_opcodes()

_TITLE_RE = re.compile(
    r"^(?P<test_file>tests/[^:]+\.py)::"
    r"(?P<test_name>[^\[]+?)"
    r"(?:\[(?P<test_params>.*)\])?$"
)

_GAS_RE = re.compile(r"bench_(\d+)_gas")


def _find_opcode_in_params(params: str | float) -> str | None:
    if not isinstance(params, str) or not params:
        return None
    for tok in params.split("-"):
        if tok in _OPCODES:
            return tok
    return None


def _compute_opcode(name: str | float, params: str | float) -> str | None:
    if not isinstance(name, str) or not name:
        return None
    params_str = params if isinstance(params, str) else ""

    specials: dict[str, str] = {
        "test_keccak": "KECCAK256",
        "test_jumpdests": "JUMPDEST",
        "test_ripemd160": "RIPEMD-160",
        "test_sha256": "SHA2-256",
        "test_point_evaluation": "POINT_EVALUATION",
        "test_bls12_fp_to_g1": "BLS12_MAP_FP_TO_G1",
        "test_bls12_fp_to_g2": "BLS12_MAP_FP2_TO_G2",
        "test_bls12_pairing_uncachable": "BLS12_PAIRING_CHECK",
        "test_ec_pairing": "ECPAIRING",
    }
    if name in specials:
        return specials[name]

    first_param = params_str.split("-")[0] if params_str else ""

    if name == "test_alt_bn128_uncachable":
        return {"add": "ECADD", "mul": "ECMUL"}.get(first_param)

    if name == "test_bls12_381_uncachable":
        return f"BLS12_{first_param.upper()}" if first_param else None

    if name == "test_storage_access":
        return first_param or None

    if name == "test_sstore":
        return "SSTORE"

    found = _find_opcode_in_params(params_str)
    return found


def parse_test_titles(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Parse a DataFrame's `test_title` column into structured columns.

    Adds `test_file`, `test_name`, `test_opcode`, `test_params`, and
    `block_limit_million` columns. Titles that don't match the
    `tests/.../<file>.py::<test_name>` shape get empty parsed columns and
    are returned in the second tuple element so the caller can warn.

    Args:
        df: DataFrame with a `test_title` column. Not mutated.

    Returns:
        Tuple of (parsed DataFrame, list of titles that did not match the
        fixture shape).
    """
    out = df.copy()
    titles = out["test_title"].astype(str)

    extracted = titles.str.extract(_TITLE_RE)
    matched_mask = extracted["test_file"].notna()

    out["test_file"] = extracted["test_file"].fillna("").astype(object)
    out["test_name"] = extracted["test_name"].fillna("").astype(object)
    out["test_params"] = extracted["test_params"].fillna("").astype(object)

    opcodes = [
        _compute_opcode(name, params) if name else ""
        for name, params in zip(out["test_name"], out["test_params"], strict=True)
    ]
    opcodes = [op if op else "" for op in opcodes]
    out["test_opcode"] = pd.Series(opcodes, index=out.index, dtype=object)

    gas_match = titles.str.extract(_GAS_RE)
    gas_values = pd.to_numeric(gas_match[0], errors="coerce")
    blm_series = (gas_values // 1_000_000).astype("Int64")
    out["block_limit_million"] = blm_series

    bls12_mask = out["test_name"] == "test_bls12_381_uncachable"
    out.loc[bls12_mask, "test_params"] = ""

    unparsed: list[str] = titles[~matched_mask].tolist()
    return out, unparsed


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python -m benchmarkoor_fetch.parse.titles <titles.txt>",
            file=sys.stderr,
        )
        return 2
    path = Path(argv[1])
    titles = [line for line in path.read_text().splitlines() if line.strip()]
    df = pd.DataFrame({"test_title": titles})
    parsed, _ = parse_test_titles(df)
    cols = [
        "test_title",
        "test_file",
        "test_name",
        "test_opcode",
        "test_params",
    ]
    parsed[cols].to_csv(sys.stdout, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
