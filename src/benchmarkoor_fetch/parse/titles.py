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
    r"^(?P<test_file>test_[^.]+\.py)__"
    r"(?P<test_name>[^\[]+?)"
    r"(?:\[(?P<test_params>.*)\])?$"
)

_BLOCK_LIMIT_RE = re.compile(r"benchmark_(\d+)M")

_OPCODE_TOKEN_RE = re.compile(r"^opcode_(.+)$")

# Tests whose opcode is fully determined by `test_name`.
_NAME_TO_OPCODE: dict[str, str] = {
    "test_alt_bn128_benchmark": "ECPAIRING",
    "test_blake2f_benchmark": "BLAKE2F",
    "test_blake2f_uncachable": "BLAKE2F",
    "test_blockhash": "BLOCKHASH",
    "test_bls12_g1_msm": "BLS12_G1MSM",
    "test_bls12_g2_msm": "BLS12_G2MSM",
    "test_bls12_pairing": "BLS12_PAIRING_CHECK",
    "test_bls12_pairing_uncachable": "BLS12_PAIRING_CHECK",
    "test_calldatacopy_from_origin": "CALLDATACOPY",
    "test_calldataload": "CALLDATALOAD",
    "test_calldatasize": "CALLDATASIZE",
    "test_callvalue_from_origin": "CALLVALUE",
    "test_clz_same": "CLZ",
    "test_codecopy_benchmark": "CODECOPY",
    "test_codesize": "CODESIZE",
    "test_ec_pairing": "ECPAIRING",
    "test_ecrecover": "ECRECOVER",
    "test_ether_transfers_onchain_receivers": "ETH_TRANSFER",
    "test_gas_op": "GAS",
    "test_identity_fixed_size": "IDENTITY",
    "test_identity_uncachable": "IDENTITY",
    "test_iszero": "ISZERO",
    "test_jump_benchmark": "JUMP",
    "test_jumpdests": "JUMPDEST",
    "test_jumpi_fallthrough": "JUMPI",
    "test_keccak_diff_mem_msg_sizes": "KECCAK256",
    "test_mcopy": "MCOPY",
    "test_modexp_uncachable": "MODEXP",
    "test_msize": "MSIZE",
    "test_not_op": "NOT",
    "test_p256verify": "P256VERIFY",
    "test_p256verify_uncachable": "P256VERIFY",
    "test_pc_op": "PC",
    "test_point_evaluation": "POINT_EVALUATION",
    "test_point_evaluation_uncachable": "POINT_EVALUATION",
    "test_returndatacopy": "RETURNDATACOPY",
    "test_returndatasize_nonzero": "RETURNDATASIZE",
    "test_ripemd160_fixed_size": "RIPEMD-160",
    "test_ripemd160_uncachable": "RIPEMD-160",
    "test_selfbalance": "SELFBALANCE",
    "test_sha256_fixed_size": "SHA2-256",
    "test_sha256_uncachable": "SHA2-256",
    "test_sload_bloated": "SLOAD",
    "test_sstore_bloated": "SSTORE",
    "test_storage_sload_same_key_benchmark": "SLOAD",
    "test_tload": "TLOAD",
    "test_tstore": "TSTORE",
}

_BLS12_PARAM_TO_OPCODE: dict[str, str] = {
    "bls12_fp_to_g1": "BLS12_MAP_FP_TO_G1",
    "bls12_fp_to_g2": "BLS12_MAP_FP2_TO_G2",
    "bls12_g1add": "BLS12_G1ADD",
    "bls12_g1msm": "BLS12_G1MSM",
    "bls12_g2add": "BLS12_G2ADD",
    "bls12_g2msm": "BLS12_G2MSM",
}


def _opcode_from_params(name: str, params: str) -> str | None:
    tokens = params.split("-") if params else []

    if name in ("test_bls12_381", "test_bls12_381_uncachable"):
        for tok in tokens:
            if tok in _BLS12_PARAM_TO_OPCODE:
                return _BLS12_PARAM_TO_OPCODE[tok]
        return None

    # bn128_add* and bn128_double both map to ECADD (point doubling uses ECADD).
    if name == "test_alt_bn128":
        for tok in tokens:
            if tok.startswith("bn128_add") or tok == "bn128_double":
                return "ECADD"
            if tok.startswith("bn128_mul"):
                return "ECMUL"
        return None

    if name == "test_alt_bn128_uncachable":
        for tok in tokens:
            if tok == "ec_add":
                return "ECADD"
            if tok.startswith("ec_mul"):
                return "ECMUL"
        return None

    # SLOAD / SSTORE benchmarks: first non-fork/benchmark token is the opcode,
    # possibly with a suffix ("SSTORE_new", "SSTORE new value").
    if name in (
        "test_storage_access_cold_benchmark",
        "test_storage_access_warm_benchmark",
    ):
        for tok in tokens:
            if tok.startswith(("fork_", "benchmark_")):
                continue
            head = re.split(r"[_ ]", tok, maxsplit=1)[0]
            if head in _OPCODES:
                return head
        return None

    return None


def _opcode_from_opcode_token(params: str) -> str | None:
    if not params:
        return None
    for tok in params.split("-"):
        match = _OPCODE_TOKEN_RE.match(tok)
        if match and match.group(1) in _OPCODES:
            return match.group(1)
    return None


def _compute_opcode(name: str | float, params: str | float) -> str | None:
    if not isinstance(name, str) or not name:
        return None
    params_str = params if isinstance(params, str) else ""

    if name in _NAME_TO_OPCODE:
        return _NAME_TO_OPCODE[name]

    by_param = _opcode_from_params(name, params_str)
    if by_param is not None:
        return by_param

    return _opcode_from_opcode_token(params_str)


def parse_test_titles(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Parse a DataFrame's `test_title` column into structured columns.

    Adds `test_file`, `test_name`, `test_opcode`, `test_params`, and
    `block_limit_million` columns. Titles that don't match the
    `<file>.py__<test_name>` shape get empty parsed columns and are
    returned in the second tuple element so the caller can warn.

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
        _compute_opcode(name, params) or ""
        for name, params in zip(out["test_name"], out["test_params"], strict=True)
    ]
    out["test_opcode"] = pd.Series(opcodes, index=out.index, dtype=object)

    blm_match = titles.str.extract(_BLOCK_LIMIT_RE)
    out["block_limit_million"] = pd.to_numeric(blm_match[0], errors="coerce").astype(
        "Int64"
    )

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
