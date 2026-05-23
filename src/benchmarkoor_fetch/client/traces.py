from __future__ import annotations

from typing import Any

import requests


def fetch_trace(
    session: requests.Session,
    *,
    suite_hash: str,
    base_url: str,
) -> dict[str, Any]:
    """GET the per-suite trace summary blob (`/files/<hash>/summary.json`)."""
    response = session.get(f"{base_url}/files/{suite_hash}/summary.json")
    response.raise_for_status()
    return response.json()
