from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd
import requests

_WIRE_COLUMNS = (
    "run_id",
    "client_name",
    "test_title",
    "run_duration_ms",
    "ingestion_timestamp",
)
_FRAME_COLUMNS = (
    "run_id",
    "client_name",
    "test_title",
    "test_runtime_ms",
    "ingestion_timestamp",
)


def _read_total(payload: dict[str, Any]) -> int:
    """Read the total row count from either top-level or nested pagination."""
    if "pagination" in payload and isinstance(payload["pagination"], dict):
        return int(payload["pagination"].get("total", 0))
    return int(payload.get("total", 0))


def _fetch_page(
    session: requests.Session,
    url: str,
    run_id: str,
    page: int,
    page_size: int,
) -> list[dict[str, Any]]:
    response = session.get(
        url,
        params={"run_id": run_id, "page": page, "page_size": page_size},
    )
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("data", []))


def _count_probe(
    session: requests.Session,
    url: str,
    run_id: str,
    page_size: int,
) -> int:
    response = session.get(
        url,
        params={"run_id": run_id, "page": 1, "page_size": page_size},
        headers={"Prefer": "count=exact"},
    )
    response.raise_for_status()
    return _read_total(response.json())


def fetch_test_stats_for_run(
    session: requests.Session,
    *,
    run_id: str,
    page_size: int,
    max_workers: int,
    base_url: str,
) -> pd.DataFrame:
    """Fetch every page of /test_stats for one run_id; return a raw-shape DataFrame.

    The server-side page size may be smaller than the client-requested
    `page_size` (e.g. when the API caps individual pages). To handle that
    case robustly we paginate until we either have `total` rows or see an
    empty page, instead of relying solely on `ceil(total / page_size)`.
    """
    url = f"{base_url}/test_stats"
    total = _count_probe(session, url, run_id, page_size)
    if total <= 0:
        return pd.DataFrame(columns=list(_WIRE_COLUMNS))

    expected_pages = max(1, math.ceil(total / page_size))
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for page_rows in pool.map(
            lambda p: _fetch_page(session, url, run_id, p, page_size),
            range(1, expected_pages + 1),
        ):
            rows.extend(page_rows)

    # If the API returned shorter pages than requested, keep paging until
    # we've collected `total` rows or hit an empty page.
    next_page = expected_pages + 1
    while len(rows) < total:
        page_rows = _fetch_page(session, url, run_id, next_page, page_size)
        if not page_rows:
            break
        rows.extend(page_rows)
        next_page += 1

    if not rows:
        return pd.DataFrame(columns=list(_WIRE_COLUMNS))
    return pd.DataFrame(rows)


def fetch_test_stats(
    session: requests.Session,
    *,
    run_ids: list[str],
    page_size: int,
    max_workers: int,
    base_url: str,
) -> pd.DataFrame:
    """Fetch /test_stats for each run_id sequentially; pages within a run are parallel.

    The wire column `run_duration_ms` is renamed to `test_runtime_ms` on the
    returned frame; the wire shape is preserved per-run so callers (the cache)
    can still see the original column names if they hook in earlier.
    """
    if not run_ids:
        empty = pd.DataFrame(columns=list(_FRAME_COLUMNS))
        return empty

    parts: list[pd.DataFrame] = []
    for run_id in run_ids:
        frame = fetch_test_stats_for_run(
            session,
            run_id=run_id,
            page_size=page_size,
            max_workers=max_workers,
            base_url=base_url,
        )
        parts.append(frame)

    combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if "run_duration_ms" in combined.columns:
        combined = combined.rename(columns={"run_duration_ms": "test_runtime_ms"})
    for col in _FRAME_COLUMNS:
        if col not in combined.columns:
            combined[col] = pd.Series(dtype=object)
    return combined
