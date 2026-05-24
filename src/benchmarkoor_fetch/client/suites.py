from __future__ import annotations

import re
from typing import Any

import requests

_SUITE_NAME_RE = re.compile(r"^(.+)-(\d{2,})-([^-]+)-([^-]+)$")


def _parse_suite_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Augment a raw suite record with parsed network/fork/test_type fields.

    Returns None if `name` does not match the expected
    `<network>-<digits>-<fork>-<test_type>` format.
    """
    name = record.get("name")
    if not isinstance(name, str):
        return None
    match = _SUITE_NAME_RE.match(name)
    if match is None:
        return None
    return {
        **record,
        "network": match.group(1).replace("-", "_"),
        "fork": match.group(3),
        "test_type": match.group(4),
    }


def _fetch_repricings_suites(
    session: requests.Session, *, base_url: str, page_size: int
) -> list[dict[str, Any]]:
    response = session.get(
        f"{base_url}/suites",
        params={"discovery_path": "eq.repricings/results", "limit": page_size},
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return list(data)


def list_suites(
    session: requests.Session,
    *,
    network: str,
    fork: str,
    test_type: str,
    base_url: str,
    page_size: int,
) -> list[dict[str, Any]]:
    """Return all suite entries matching the (network, fork, test_type) tuple."""
    raw = _fetch_repricings_suites(session, base_url=base_url, page_size=page_size)
    out: list[dict[str, Any]] = []
    for record in raw:
        parsed = _parse_suite_record(record)
        if parsed is None:
            continue
        if (
            parsed["network"] == network
            and parsed["fork"] == fork
            and parsed["test_type"] == test_type
        ):
            out.append(parsed)
    return out


def resolve_suite(
    session: requests.Session,
    *,
    network: str,
    fork: str,
    test_type: str,
    base_url: str,
    page_size: int,
) -> str:
    """Return the `suite_hash` of the latest indexed matching suite."""
    suites = list_suites(
        session,
        network=network,
        fork=fork,
        test_type=test_type,
        base_url=base_url,
        page_size=page_size,
    )
    if not suites:
        raise RuntimeError(
            f"no suites matched network={network!r} fork={fork!r} "
            f"test_type={test_type!r}"
        )
    latest = max(suites, key=lambda s: s.get("indexed_at", ""))
    return str(latest["suite_hash"])
