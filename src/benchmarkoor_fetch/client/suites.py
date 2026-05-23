from __future__ import annotations

from typing import Any

import requests


def list_suites(
    session: requests.Session,
    *,
    network: str,
    fork: str,
    test_type: str,
    base_url: str,
) -> list[dict[str, Any]]:
    """Return all suite entries matching the (network, fork, test_type) tuple."""
    response = session.get(
        f"{base_url}/suites",
        params={"network": network, "fork": fork, "test_type": test_type},
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return list(data)


def resolve_suite(
    session: requests.Session,
    *,
    network: str,
    fork: str,
    test_type: str,
    base_url: str,
) -> str:
    """Return the `suite_hash` of the latest indexed matching suite."""
    suites = list_suites(
        session,
        network=network,
        fork=fork,
        test_type=test_type,
        base_url=base_url,
    )
    if not suites:
        raise RuntimeError(
            f"no suites matched network={network!r} fork={fork!r} "
            f"test_type={test_type!r}"
        )
    latest = max(suites, key=lambda s: s.get("indexed_at", ""))
    return str(latest["suite_hash"])
