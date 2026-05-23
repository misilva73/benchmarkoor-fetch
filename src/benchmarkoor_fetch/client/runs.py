from __future__ import annotations

from typing import Any

import requests


def list_runs(
    session: requests.Session,
    *,
    suite_hash: str,
    base_url: str,
) -> list[dict[str, Any]]:
    """Return every run record for a suite. No server-side filtering.

    The Benchmarkoor `/runs` endpoint only honours `suite_hash`; date-window
    and run_type narrowing happens client-side via `filter_runs`.
    """
    response = session.get(f"{base_url}/runs", params={"suite_hash": suite_hash})
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return list(payload.get("data", []))
    return list(payload)


def filter_runs(
    runs: list[dict[str, Any]],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    run_type: str | None = None,
) -> list[dict[str, Any]]:
    """Apply start_date / end_date / run_type filters in-process.

    `run_type` is derived from the trailing `-` segment of each `run_id`, matching
    the reference implementation (`evm-gas-repricings/src/data.py`). Date bounds
    compare against the date portion of each record's `start_ts`, and `end_date`
    is inclusive.
    """
    out: list[dict[str, Any]] = []
    for record in runs:
        if run_type is not None:
            rid = str(record.get("run_id", ""))
            if rid.rsplit("-", 1)[-1] != run_type:
                continue
        if start_date is not None or end_date is not None:
            start_ts = record.get("start_ts")
            if not isinstance(start_ts, str):
                continue
            day = start_ts[:10]
            if start_date is not None and day < start_date:
                continue
            if end_date is not None and day > end_date:
                continue
        out.append(record)
    return out
