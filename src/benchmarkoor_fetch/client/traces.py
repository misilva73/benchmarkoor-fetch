from __future__ import annotations

from typing import Any

import requests


def fetch_trace_raw(
    session: requests.Session,
    *,
    suite_hash: str,
    files_base_url: str,
) -> dict[str, Any]:
    """GET the per-suite `summary.json` and return the raw payload.

    The URL lives under `/api/v1/files/repricings/results/suites/<hash>/summary.json`
    with `redirect=true` to follow the storage redirect server-side.
    """
    url = f"{files_base_url}/repricings/results/suites/{suite_hash}/summary.json"
    response = session.get(url, params={"redirect": "true"})
    response.raise_for_status()
    return response.json()


def transform_trace(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Convert a raw `summary.json` payload into `{test_title: {op: count}}`.

    Tests without an `opcode_count` block are skipped. The `.txt` suffix is
    stripped from each `name` so titles align with `/test_stats` rows.
    """
    out: dict[str, dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    tests = raw.get("tests", [])
    if not isinstance(tests, list):
        return out
    for test in tests:
        if not isinstance(test, dict):
            continue
        opcode_count = test.get("opcode_count")
        if not isinstance(opcode_count, dict):
            continue
        name = test.get("name", "")
        if not isinstance(name, str):
            continue
        title = name.split(".txt")[0]
        out[title] = {str(k): float(v) for k, v in opcode_count.items()}
    return out
