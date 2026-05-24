from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import requests

_WIRE_COLUMNS = (
    "run_id",
    "test_name",
    "client",
    "test_time_ns",
    "run_start",
)
_FRAME_COLUMNS = (
    "run_id",
    "client_name",
    "test_title",
    "test_runtime_ms",
    "ingestion_timestamp",
)

_BASE_PARAMS: dict[str, Any] = {
    "select": "run_id,test_name,client,test_time_ns,run_start",
    "test_time_ns": "gt.0",
}


def _read_total(payload: dict[str, Any]) -> int:
    """Read the total row count returned by `Prefer: count=exact`."""
    return int(payload.get("total", 0))


def _get_total(session: requests.Session, url: str, params: dict[str, Any]) -> int:
    response = session.get(
        url,
        params={**params, "limit": 0},
        headers={"Prefer": "count=exact"},
    )
    response.raise_for_status()
    return _read_total(response.json())


def _fetch_page(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    page: int,
    page_size: int,
) -> tuple[int, list[dict[str, Any]]]:
    paginated = {**params, "limit": page_size, "offset": page * page_size}
    response = session.get(url, params=paginated)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return page, list(data)


def normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename wire columns to the package's output schema and convert units.

    `test_time_ns` (ns) → `test_runtime_ms` (ms); `client` → `client_name`;
    `test_name` → `test_title` (with `.txt` stripped); `run_start` (Unix
    seconds) → `ingestion_timestamp` (UTC datetime).
    """
    if frame.empty:
        return pd.DataFrame(columns=list(_FRAME_COLUMNS))
    frame = frame.copy()
    if "test_time_ns" in frame.columns:
        frame["test_runtime_ms"] = frame["test_time_ns"] / 1_000_000
        frame = frame.drop(columns=["test_time_ns"])
    frame = frame.rename(
        columns={
            "client": "client_name",
            "test_name": "test_title",
            "run_start": "ingestion_timestamp",
        }
    )
    if "ingestion_timestamp" in frame.columns:
        frame["ingestion_timestamp"] = pd.to_datetime(
            frame["ingestion_timestamp"], unit="s", utc=True
        )
    if "test_title" in frame.columns:
        frame["test_title"] = (
            frame["test_title"].astype(str).str.replace(".txt", "", regex=False)
        )
    return frame


def fetch_test_stats_for_run_raw(
    session: requests.Session,
    *,
    run_id: str,
    page_size: int,
    max_workers: int,
    base_url: str,
) -> pd.DataFrame:
    """Fetch /test_stats for one run_id; return the raw wire DataFrame.

    The total row count is fetched first via `Prefer: count=exact` + `limit=0`,
    then pages are pulled in parallel via offset-based pagination.
    """
    url = f"{base_url}/test_stats"
    params = {**_BASE_PARAMS, "run_id": f"eq.{run_id}"}

    total = _get_total(session, url, params)
    if total <= 0:
        return pd.DataFrame(columns=list(_WIRE_COLUMNS))

    total_pages = max(1, math.ceil(total / page_size))
    all_data: list[list[dict[str, Any]]] = [[] for _ in range(total_pages)]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_page, session, url, params, page, page_size): page
            for page in range(total_pages)
        }
        for future in as_completed(futures):
            page, rows = future.result()
            all_data[page] = rows

    flat = [row for page_rows in all_data for row in page_rows]
    if not flat:
        return pd.DataFrame(columns=list(_WIRE_COLUMNS))
    return pd.DataFrame(flat)


def fetch_test_stats(
    session: requests.Session,
    *,
    run_ids: list[str],
    page_size: int,
    max_workers: int,
    base_url: str,
) -> pd.DataFrame:
    """Fetch /test_stats for each run_id sequentially; pages within a run are parallel.

    Returns the normalised (output-shape) DataFrame.
    """
    if not run_ids:
        return pd.DataFrame(columns=list(_FRAME_COLUMNS))

    parts: list[pd.DataFrame] = []
    for run_id in run_ids:
        raw = fetch_test_stats_for_run_raw(
            session,
            run_id=run_id,
            page_size=page_size,
            max_workers=max_workers,
            base_url=base_url,
        )
        parts.append(raw)

    combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return normalise_columns(combined)
