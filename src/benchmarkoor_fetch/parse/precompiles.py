from __future__ import annotations

_PRECOMPILES_BASE: frozenset[str] = frozenset(
    {
        "IDENTITY",
        "SHA2-256",
        "RIPEMD-160",
        "MODEXP",
        "BLAKE2F",
        "ECADD",
        "ECMUL",
        "ECPAIRING",
        "KECCAK256",
        "POINT_EVALUATION",
        "BLS12_G1ADD",
        "BLS12_G1MSM",
        "BLS12_G2ADD",
        "BLS12_G2MSM",
        "BLS12_PAIRING_CHECK",
        "BLS12_MAP_FP_TO_G1",
        "BLS12_MAP_FP2_TO_G2",
    }
)


def get_precompiles(fork: str) -> set[str]:
    """Return the set of precompile opcode names active for the given fork.

    Fork name is matched case-insensitively. Forks not explicitly known fall
    back to the most recent set.
    """
    _ = fork.lower() if isinstance(fork, str) else fork
    return set(_PRECOMPILES_BASE)
