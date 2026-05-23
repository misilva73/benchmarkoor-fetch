from __future__ import annotations

from typing import Any

import requests


def list_runs(
    session: requests.Session,
    *,
    suite_hash: str,
    start_date: str | None = None,
    end_date: str | None = None,
    run_type: str | None = None,
    base_url: str,
) -> list[dict[str, Any]]:
    """Return run records for a suite, optionally filtered by window and run_type.

    None-valued filters are omitted from the request URL entirely so the
    backend sees the same shape the user intended.
    """
    params: dict[str, str] = {"suite_hash": suite_hash}
    if start_date is not None:
        params["start_ts"] = str(start_date)
    if end_date is not None:
        params["end_ts"] = str(end_date)
    if run_type is not None:
        params["run_type"] = str(run_type)

    response = session.get(f"{base_url}/runs", params=params)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return list(payload.get("data", []))
    return list(payload)
