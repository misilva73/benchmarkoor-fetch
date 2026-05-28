from __future__ import annotations

import re
from typing import Any

import pandas as pd
import requests


def _start_date_to_unix(start_date: str) -> int:
    return int(pd.Timestamp(start_date).timestamp())


def _unix_to_iso(ts: int | float | str) -> str:
    """Convert a Unix timestamp (seconds) to ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`)."""
    return pd.to_datetime(int(float(ts)), unit="s", utc=True).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def list_runs(
    session: requests.Session,
    *,
    suite_hash: str,
    base_url: str,
    start_date: str | None = None,
    page_size: int,
) -> list[dict[str, Any]]:
    """Return every completed run for a suite, paginated.

    `start_date` narrows results server-side via `timestamp=gt.{unix_ts}`.
    `end_date` and `run_id_pattern` are applied in-process by `filter_runs`.

    Each returned record has shape `{run_id, timestamp, start_ts}`, where
    `start_ts` is the ISO form of `timestamp` (for downstream consumers).
    """
    params: dict[str, Any] = {
        "select": "run_id,timestamp",
        "suite_hash": f"eq.{suite_hash}",
        "status": "eq.completed",
        "limit": page_size,
    }
    if start_date is not None:
        params["timestamp"] = f"gt.{_start_date_to_unix(start_date)}"

    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        paginated = {**params, "offset": offset}
        response = session.get(f"{base_url}/runs", params=paginated)
        response.raise_for_status()
        payload = response.json()
        page = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not page:
            break
        for record in page:
            record_out = dict(record)
            ts = record_out.get("timestamp")
            if ts is not None:
                record_out["start_ts"] = _unix_to_iso(ts)
            out.append(record_out)
        if len(page) < page_size:
            break
        offset += page_size
    return out


def filter_runs(
    runs: list[dict[str, Any]],
    *,
    end_date: str | None = None,
    run_id_pattern: str | None = None,
) -> list[dict[str, Any]]:
    """Apply end_date / run_id_pattern filters in-process.

    `run_id_pattern` is a regex matched against each `run_id` via
    `re.fullmatch` — the whole `run_id` must match. `end_date` is compared
    against the date portion of `start_ts` (inclusive).
    """
    compiled = re.compile(run_id_pattern) if run_id_pattern is not None else None
    out: list[dict[str, Any]] = []
    for record in runs:
        if compiled is not None:
            rid = str(record.get("run_id", ""))
            if compiled.fullmatch(rid) is None:
                continue
        if end_date is not None:
            start_ts = record.get("start_ts")
            if not isinstance(start_ts, str):
                continue
            day = start_ts[:10]
            if day > end_date:
                continue
        out.append(record)
    return out
