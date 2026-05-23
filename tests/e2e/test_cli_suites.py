"""End-to-end tests for `benchmarkoor-fetch suites`."""

from __future__ import annotations

from tests.e2e.conftest import (
    CANONICAL_SUITE_HASH,
    MockedApi,
    Runner,
    register_suites,
)


def test_suites_prints_resolved_hash(
    mocked_api: MockedApi,
    runner: Runner,
) -> None:
    """Scenario #10: `suites` prints resolved hash + indexed_at; no other calls."""

    result = runner.invoke(
        "suites",
        "--network",
        "jochemnet",
        "--fork",
        "amsterdam",
        "--test-type",
        "compute",
    )

    assert result.exit_code == 0, result.stderr

    assert CANONICAL_SUITE_HASH in result.stdout, (
        f"resolved hash not found in stdout: {result.stdout!r}"
    )
    # `indexed_at` of the latest matching suite from the canonical fixture.
    assert "2026-05-17" in result.stdout, f"indexed_at not in stdout: {result.stdout!r}"

    # No /runs or /test_stats traffic.
    assert mocked_api.call_count("/runs") == 0
    assert mocked_api.call_count("/test_stats") == 0


def test_suites_picks_latest_indexed_at(
    mocked_api_raw: MockedApi,
    runner: Runner,
) -> None:
    """Scenario #11: with two matching suites, prints the later-indexed one."""

    later_hash = "0xLATER000000000000000000000000000000000000000000000000000000000000"
    body = {
        "data": [
            {
                "suite_hash": (
                    "0xEARLIER0000000000000000000000000000000000000000000000000000000000"
                ),
                "name": "older-suite",
                "network": "jochemnet",
                "fork": "amsterdam",
                "test_type": "compute",
                "indexed_at": "2026-04-01T00:00:00Z",
            },
            {
                "suite_hash": later_hash,
                "name": "newer-suite",
                "network": "jochemnet",
                "fork": "amsterdam",
                "test_type": "compute",
                "indexed_at": "2026-05-01T00:00:00Z",
            },
        ]
    }
    register_suites(mocked_api_raw.rsps, body=body)

    result = runner.invoke(
        "suites",
        "--network",
        "jochemnet",
        "--fork",
        "amsterdam",
        "--test-type",
        "compute",
    )

    assert result.exit_code == 0, result.stderr
    assert later_hash in result.stdout, (
        f"expected later hash {later_hash} in stdout; got {result.stdout!r}"
    )
