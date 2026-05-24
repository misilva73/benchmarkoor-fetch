"""Unit tests for `benchmarkoor_fetch.client` HTTP behaviour.

All HTTP is mocked via `responses`. No real network access. Covers session
construction, request shapes, pagination math, retry semantics, auth
precedence, and the Style-B `client.parse` wrapper.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import responses

from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig
from benchmarkoor_fetch.client import session as session_module

DATA_DIR = Path(__file__).parent / "data" / "http"
BENCHMARKOOR_HOST = "https://benchmarkoor-api.core.ethpandaops.io"
BASE_URL = f"{BENCHMARKOOR_HOST}/api/v1/index/query"
FILES_BASE_URL = f"{BENCHMARKOOR_HOST}/api/v1/files"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provide a stable bearer token via env var unless a test overrides it."""
    monkeypatch.setenv("BENCHMARKOOR_TOKEN", "test-token")
    return "test-token"


@pytest.fixture
def basic_config() -> FetchConfig:
    return FetchConfig(
        query={
            "network": "jochemnet",
            "fork": "amsterdam",
            "test_type": "compute",
        }
    )


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text())


# --------------------------------------------------------------------------- #
# Scenario #31: build_session wires urllib3.Retry from config
# --------------------------------------------------------------------------- #


def test_build_session_wires_retry_from_config(basic_config: FetchConfig) -> None:
    """Scenario #31: HTTP retry params propagate from config to the adapter."""
    config = basic_config.with_cli_overrides()  # noqa: F841 — exercise the call path
    session = session_module.build_session(basic_config.http)
    adapter = session.get_adapter("https://example.com/")
    retry = adapter.max_retries
    assert retry.total == basic_config.http.retries
    assert retry.backoff_factor == basic_config.http.backoff_factor
    assert list(retry.status_forcelist) == list(basic_config.http.retry_status)


# --------------------------------------------------------------------------- #
# Scenario #32: resolve_suite request shape
# --------------------------------------------------------------------------- #


@responses.activate
def test_resolve_suite_request_shape(token: str, basic_config: FetchConfig) -> None:
    """Scenario #32: GET /suites with discovery_path filter and bearer header."""
    suites_body = _load_json("suites_two_matching.json")
    responses.add(
        responses.GET,
        f"{BASE_URL}/suites",
        json=suites_body,
        status=200,
    )

    client = BenchmarkoorClient(token=token)
    client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")

    assert len(responses.calls) == 1
    call = responses.calls[0]
    url = call.request.url
    assert "/suites" in url
    # PostgREST-style discovery filter is sent to the server.
    assert "discovery_path=eq.repricings%2Fresults" in url or (
        "discovery_path=eq.repricings/results" in url
    )
    assert call.request.headers.get("Authorization") == f"Bearer {token}"


# --------------------------------------------------------------------------- #
# Scenario #33: resolve_suite picks latest by indexed_at
# --------------------------------------------------------------------------- #


@responses.activate
def test_resolve_suite_picks_latest_indexed_at(token: str) -> None:
    """Scenario #33: with two matching suites, pick the later indexed_at."""
    body = _load_json("suites_two_matching.json")
    responses.add(responses.GET, f"{BASE_URL}/suites", json=body, status=200)

    client = BenchmarkoorClient(token=token)
    suite_hash = client.resolve_suite(
        network="jochemnet", fork="amsterdam", test_type="compute"
    )
    assert suite_hash == "0xbbb222"


# --------------------------------------------------------------------------- #
# Scenario #34: list_runs sends suite_hash + status + timestamp; run_type stays off wire
# --------------------------------------------------------------------------- #


@responses.activate
def test_list_runs_sends_suite_hash_status_and_timestamp_on_wire(token: str) -> None:
    """Scenario #34: /runs gets `suite_hash`, `status=completed`, `timestamp` filter."""
    body = _load_json("runs_three.json")
    responses.add(responses.GET, f"{BASE_URL}/runs", json=body, status=200)

    client = BenchmarkoorClient(token=token)
    client.list_runs(
        suite_hash="0xbbb222",
        start_date="2026-05-18",
        end_date="2026-05-20",
        run_type="full",
    )
    url = responses.calls[0].request.url
    assert "suite_hash=eq.0xbbb222" in url
    assert "status=eq.completed" in url
    # start_date is converted to a unix-ts `timestamp=gt.…` filter; end_date and
    # run_type stay client-side.
    assert "timestamp=gt." in url
    assert "end_date=" not in url
    assert "run_type=" not in url


@responses.activate
def test_list_runs_filters_records_client_side(token: str) -> None:
    """Scenario #34: client-side end_date/run_type drops out-of-window rows."""
    body = {
        "data": [
            # 2026-05-17 → before window (filtered server-side via timestamp filter)
            # 2026-05-19 full → kept
            {"run_id": "run-002-full", "timestamp": 1779181200},
            # 2026-05-19 warmup → wrong run_type
            {"run_id": "run-003-warmup", "timestamp": 1779181200},
            # 2026-05-21 → after end_date
            {"run_id": "run-004-full", "timestamp": 1779354000},
        ]
    }
    responses.add(responses.GET, f"{BASE_URL}/runs", json=body, status=200)

    client = BenchmarkoorClient(token=token)
    filtered = client.list_runs(
        suite_hash="0xbbb222",
        start_date="2026-05-18",
        end_date="2026-05-20",
        run_type="full",
    )
    assert [r["run_id"] for r in filtered] == ["run-002-full"]


# --------------------------------------------------------------------------- #
# Scenario #35: fetch_test_stats pagination matrix
# --------------------------------------------------------------------------- #


def _wire_rows(
    run_id: str, page: int, count: int, page_size: int
) -> list[dict[str, Any]]:
    """Build `count` wire rows for a given page; uses the new wire schema."""
    return [
        {
            "run_id": run_id,
            "client": "geth",
            "test_name": f"t-{page}-{i}",
            "test_time_ns": (100 + i) * 1_000_000,
            "run_start": 1779074400,
        }
        for i in range(count)
    ]


@pytest.mark.parametrize(
    ("total", "page_size", "expected_page_requests"),
    [
        (20, 10, 2),
        (25, 10, 3),
        (0, 10, 0),
        (10, 10, 1),
    ],
    ids=["exact-boundary-20-10", "uneven-25-10", "empty-0", "single-page-10-10"],
)
@responses.activate
def test_fetch_test_stats_pagination_matrix(
    token: str,
    total: int,
    page_size: int,
    expected_page_requests: int,
) -> None:
    """Scenario #35: page-request count matches ceil(total/page_size).

    Also locks the count-header round-trip (Prefer: count=exact) and the
    empty-result shape (zero pages, empty DataFrame, not None / exception).
    """
    # Count probe: returns the total via the body when `limit=0`.
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={"total": total},
        status=200,
        match=[
            responses.matchers.query_param_matcher({"limit": "0"}, strict_match=False)
        ],
    )
    # Per-page responses.
    for page in range(expected_page_requests):
        rows_on_page = min(page_size, total - page * page_size)
        responses.add(
            responses.GET,
            f"{BASE_URL}/test_stats",
            json={"data": _wire_rows("run-001-full", page, rows_on_page, page_size)},
            status=200,
            match=[
                responses.matchers.query_param_matcher(
                    {"offset": str(page * page_size)}, strict_match=False
                )
            ],
        )

    client = BenchmarkoorClient(token=token)
    df = client.fetch_test_stats(run_ids=["run-001-full"], page_size=page_size)

    count_probes = [
        c
        for c in responses.calls
        if c.request.headers.get("Prefer", "") == "count=exact"
    ]
    page_requests = [
        c
        for c in responses.calls
        if c.request.headers.get("Prefer", "") != "count=exact"
    ]
    assert len(count_probes) >= 1, (
        "Pagination must round-trip the total via a `Prefer: count=exact` probe."
    )
    assert len(page_requests) == expected_page_requests, (
        f"Expected {expected_page_requests} page fetches for total={total}, "
        f"page_size={page_size}; got {len(page_requests)}."
    )

    assert isinstance(df, pd.DataFrame)
    if total == 0:
        assert df.empty
        for col in ("run_id", "client_name", "test_title", "test_runtime_ms"):
            assert col in df.columns


# --------------------------------------------------------------------------- #
# Scenario #36c: test_time_ns → test_runtime_ms rename + unit conversion
# --------------------------------------------------------------------------- #


@responses.activate
def test_fetch_test_stats_renames_test_time_ns_to_runtime_ms(token: str) -> None:
    """Scenario #36c: wire `test_time_ns` (ns) → DataFrame `test_runtime_ms` (ms)."""
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={"total": 1},
        status=200,
        match=[
            responses.matchers.query_param_matcher({"limit": "0"}, strict_match=False)
        ],
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={
            "data": [
                {
                    "run_id": "run-001-full",
                    "client": "geth",
                    "test_name": "t1",
                    "test_time_ns": 1_234_000_000,
                    "run_start": 1779074400,
                }
            ]
        },
        status=200,
        match=[
            responses.matchers.query_param_matcher({"offset": "0"}, strict_match=False)
        ],
    )
    client = BenchmarkoorClient(token=token)
    df = client.fetch_test_stats(run_ids=["run-001-full"], page_size=10)

    assert "test_runtime_ms" in df.columns
    assert "run_duration_ms" not in df.columns
    assert "test_time_ns" not in df.columns
    assert int(df.iloc[0]["test_runtime_ms"]) == 1234


# --------------------------------------------------------------------------- #
# Scenario #37: fetch_test_stats threading
# --------------------------------------------------------------------------- #


@responses.activate
def test_fetch_test_stats_uses_threadpool_with_max_workers(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario #37: ThreadPoolExecutor is constructed with max_workers from config."""
    import concurrent.futures as cf

    seen_kwargs: list[dict[str, Any]] = []
    real_init = cf.ThreadPoolExecutor.__init__

    def spy_init(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[no-untyped-def]
        seen_kwargs.append({"args": args, "kwargs": kwargs})
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(cf.ThreadPoolExecutor, "__init__", spy_init)

    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={"total": 30},
        status=200,
        match=[
            responses.matchers.query_param_matcher({"limit": "0"}, strict_match=False)
        ],
    )
    for page in range(3):
        responses.add(
            responses.GET,
            f"{BASE_URL}/test_stats",
            json={"data": _wire_rows("run-001-full", page, 10, 10)},
            status=200,
            match=[
                responses.matchers.query_param_matcher(
                    {"offset": str(page * 10)}, strict_match=False
                )
            ],
        )

    client = BenchmarkoorClient(token=token, max_workers=7)
    client.fetch_test_stats(run_ids=["run-001-full"], page_size=10)

    pool_inits = [
        s
        for s in seen_kwargs
        if (s["args"] and isinstance(s["args"][0], int)) or "max_workers" in s["kwargs"]
    ]
    assert pool_inits, "fetch_test_stats must use ThreadPoolExecutor for pages."
    last = pool_inits[-1]
    max_workers = last["kwargs"].get(
        "max_workers", last["args"][0] if last["args"] else None
    )
    assert max_workers == 7


# --------------------------------------------------------------------------- #
# Scenario #38: fetch_test_stats is sequential across run_ids
# --------------------------------------------------------------------------- #


@responses.activate
def test_fetch_test_stats_sequential_across_run_ids(token: str) -> None:
    """Scenario #38: the second run_id's first page comes after run_id 1 fully done."""
    call_log: list[tuple[str, str]] = []

    def make_callback(run_id: str, kind: str, page: int, total: int, page_size: int):
        def _cb(request):  # noqa: ANN001
            call_log.append((run_id, kind))
            if kind == "count":
                return (200, {}, json.dumps({"total": total}))
            count = min(page_size, total - page * page_size)
            body = {"data": _wire_rows(run_id, page, count, page_size)}
            return (200, {}, json.dumps(body))

        return _cb

    # `responses` matches callbacks in FIFO order.
    responses.add_callback(
        responses.GET,
        f"{BASE_URL}/test_stats",
        callback=make_callback("run-A", "count", 0, 20, 10),
        content_type="application/json",
    )
    responses.add_callback(
        responses.GET,
        f"{BASE_URL}/test_stats",
        callback=make_callback("run-A", "page-0", 0, 20, 10),
        content_type="application/json",
    )
    responses.add_callback(
        responses.GET,
        f"{BASE_URL}/test_stats",
        callback=make_callback("run-A", "page-1", 1, 20, 10),
        content_type="application/json",
    )
    responses.add_callback(
        responses.GET,
        f"{BASE_URL}/test_stats",
        callback=make_callback("run-B", "count", 0, 10, 10),
        content_type="application/json",
    )
    responses.add_callback(
        responses.GET,
        f"{BASE_URL}/test_stats",
        callback=make_callback("run-B", "page-0", 0, 10, 10),
        content_type="application/json",
    )

    client = BenchmarkoorClient(token=token)
    client.fetch_test_stats(run_ids=["run-A", "run-B"], page_size=10)

    runs_in_order = [entry[0] for entry in call_log]
    last_a_index = max(i for i, r in enumerate(runs_in_order) if r == "run-A")
    first_b_index = min(i for i, r in enumerate(runs_in_order) if r == "run-B")
    assert first_b_index > last_a_index, (
        "run-B requests must not begin until run-A is fully fetched."
    )


# --------------------------------------------------------------------------- #
# Scenario #39: fetch_trace URL shape
# --------------------------------------------------------------------------- #


@responses.activate
def test_fetch_trace_url_shape(token: str) -> None:
    """Scenario #39: GET …/files/repricings/results/suites/<hash>/summary.json once."""
    summary = _load_json("summary_minimal.json")
    suite_hash = "0xbbb222"
    summary_url = (
        f"{FILES_BASE_URL}/repricings/results/suites/{suite_hash}/summary.json"
    )
    responses.add(
        responses.GET,
        summary_url,
        json=summary,
        status=200,
    )
    client = BenchmarkoorClient(token=token)
    client.fetch_trace(suite_hash=suite_hash)

    assert len(responses.calls) == 1
    call_url = responses.calls[0].request.url
    assert f"/repricings/results/suites/{suite_hash}/summary.json" in call_url
    assert "redirect=true" in call_url


# --------------------------------------------------------------------------- #
# Scenario #40: 502 → 502 → 200 succeeds
# --------------------------------------------------------------------------- #


@responses.activate
def test_two_502s_then_200_succeeds(token: str) -> None:
    """Scenario #40: with retries=3, two 502s then 200 returns the body."""
    body = _load_json("suites_two_matching.json")
    responses.add(responses.GET, f"{BASE_URL}/suites", status=502)
    responses.add(responses.GET, f"{BASE_URL}/suites", status=502)
    responses.add(responses.GET, f"{BASE_URL}/suites", json=body, status=200)

    client = BenchmarkoorClient(token=token)
    result = client.resolve_suite(
        network="jochemnet", fork="amsterdam", test_type="compute"
    )
    assert result == "0xbbb222"
    assert len(responses.calls) == 3


# --------------------------------------------------------------------------- #
# Scenario #41: 502 exhausted raises
# --------------------------------------------------------------------------- #


@responses.activate
def test_retry_budget_exhausted_raises(token: str, basic_config: FetchConfig) -> None:
    """Scenario #41: more 502s than retries+1 → raises requests.HTTPError."""
    import requests

    for _ in range(basic_config.http.retries + 2):
        responses.add(responses.GET, f"{BASE_URL}/suites", status=502)

    client = BenchmarkoorClient(token=token)
    with pytest.raises((requests.HTTPError, requests.exceptions.RetryError)):
        client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")


# --------------------------------------------------------------------------- #
# Scenario #42: 401 surfaces immediately, no retry
# --------------------------------------------------------------------------- #


@responses.activate
def test_401_does_not_retry(token: str) -> None:
    """Scenario #42: 401 raises a distinguishable auth error after exactly one call."""
    import requests

    responses.add(responses.GET, f"{BASE_URL}/suites", status=401)

    client = BenchmarkoorClient(token=token)
    with pytest.raises(requests.HTTPError) as excinfo:
        client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")
    assert len(responses.calls) == 1, "401 must not trigger retries."
    assert "401" in str(excinfo.value) or "auth" in str(excinfo.value).lower()


# --------------------------------------------------------------------------- #
# Scenario #43: kwarg token beats env
# --------------------------------------------------------------------------- #


@responses.activate
def test_token_kwarg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario #43: BenchmarkoorClient(token=X) wins over BENCHMARKOOR_TOKEN=Y."""
    monkeypatch.setenv("BENCHMARKOOR_TOKEN", "env-token")
    responses.add(
        responses.GET,
        f"{BASE_URL}/suites",
        json=_load_json("suites_two_matching.json"),
        status=200,
    )
    client = BenchmarkoorClient(token="kwarg-token")
    client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")
    assert responses.calls[0].request.headers["Authorization"] == "Bearer kwarg-token"


# --------------------------------------------------------------------------- #
# Scenario #43a: env fallback when no kwarg
# --------------------------------------------------------------------------- #


@responses.activate
def test_env_token_used_when_no_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario #43a: with no kwarg, BENCHMARKOOR_TOKEN env var is used."""
    monkeypatch.setenv("BENCHMARKOOR_TOKEN", "env-token")
    responses.add(
        responses.GET,
        f"{BASE_URL}/suites",
        json=_load_json("suites_two_matching.json"),
        status=200,
    )
    client = BenchmarkoorClient()
    client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")
    assert responses.calls[0].request.headers["Authorization"] == "Bearer env-token"


# --------------------------------------------------------------------------- #
# Scenario #43b: missing token raises at construction
# --------------------------------------------------------------------------- #


def test_missing_token_raises_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario #43b: no kwarg and no env → raise at construction, name the env var."""
    monkeypatch.delenv("BENCHMARKOOR_TOKEN", raising=False)
    with pytest.raises(Exception) as excinfo:
        BenchmarkoorClient()
    assert "BENCHMARKOOR_TOKEN" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Scenario #44: read-only client never mutates remote state
# --------------------------------------------------------------------------- #


@responses.activate
def test_read_only_client_uses_no_mutating_methods(token: str) -> None:
    """Scenario #44: every client call is GET; no POST/PUT/DELETE registered."""
    responses.add(
        responses.GET,
        f"{BASE_URL}/suites",
        json=_load_json("suites_two_matching.json"),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/runs",
        json=_load_json("runs_three.json"),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/test_stats",
        json={"total": 0},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{FILES_BASE_URL}/repricings/results/suites/0xbbb222/summary.json",
        json=_load_json("summary_minimal.json"),
        status=200,
    )

    client = BenchmarkoorClient(token=token)
    client.resolve_suite(network="jochemnet", fork="amsterdam", test_type="compute")
    client.list_runs(suite_hash="0xbbb222")
    client.fetch_test_stats(run_ids=[])
    client.fetch_trace(suite_hash="0xbbb222")

    for call in responses.calls:
        assert call.request.method == "GET", (
            f"Read-only client must only issue GET, saw {call.request.method}."
        )


# --------------------------------------------------------------------------- #
# Scenario #44a: client.parse(raw_df, trace_df) Style-B wrapper
# --------------------------------------------------------------------------- #


def test_style_b_parse_wrapper_returns_bench_and_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario #44a: client.parse(raw_df, trace_df) → (bench_df, trace_df)."""
    monkeypatch.setenv("BENCHMARKOOR_TOKEN", "x")
    raw_df = pd.DataFrame(
        {
            "run_id": ["run-001"],
            "client_name": ["geth"],
            "test_title": [
                "tests/benchmarks/test_arithmetic.py::"
                "test_arithmetic[fork_Prague-ADD-warm_300_runs]"
            ],
            "test_runtime_ms": [1234],
            "ingestion_timestamp": ["2026-05-18T03:20:00Z"],
        }
    )
    trace_df = pd.read_parquet(
        Path(__file__).parent / "data" / "opcount" / "regular_opcode.parquet"
    )

    client = BenchmarkoorClient()
    bench_df, returned_trace = client.parse(raw_df, trace_df)
    assert isinstance(bench_df, pd.DataFrame)
    assert isinstance(returned_trace, pd.DataFrame)
    expected_cols = {
        "run_id",
        "client_name",
        "test_title",
        "test_file",
        "test_name",
        "test_opcode",
        "test_params",
        "test_runtime_ms",
        "ingestion_timestamp",
        "block_limit_million",
        "opcount",
    }
    missing = expected_cols - set(bench_df.columns)
    assert not missing, f"bench_df is missing columns: {missing}"
