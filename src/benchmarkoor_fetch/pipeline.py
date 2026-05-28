from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from benchmarkoor_fetch.parse.opcount import add_opcount
from benchmarkoor_fetch.parse.titles import parse_test_titles
from benchmarkoor_fetch.result import FetchResult

if TYPE_CHECKING:
    from benchmarkoor_fetch.client import BenchmarkoorClient
    from benchmarkoor_fetch.config import FetchConfig


class EmptyResultError(RuntimeError):
    """Raised when the resolved suites yield zero runs in the requested window."""


def run_pipeline(config: FetchConfig, *, client: BenchmarkoorClient) -> FetchResult:
    """Execute the full fetch + parse pipeline; return an in-memory `FetchResult`.

    Steps:
      1. Resolve suites — honour `config.query.suites` when set, otherwise
         discover the latest matching suite via `/suites`.
      2. For each suite, list runs in the configured window.
      3. For each run, fetch `/test_stats` (per-run-cached).
      4. Fetch the `/files/<hash>/summary.json` trace once per suite.
      5. Parse titles, join opcounts, derive the trace DataFrame.
      6. Compose `meta.json` content (suites, window, version, row counts,
         unparsed_fixtures); emit the unparsed-fixture warning to stderr.

    Raises:
        EmptyResultError: When the resolved suites yield no runs.
        requests.HTTPError: For HTTP failures (auth, retry exhaustion, 4xx
            other than 404 on /runs surfaced from explicit suites).
    """
    reporter = client.reporter
    reporter.info(
        f"resolving suite for network={config.query.network} "
        f"fork={config.query.fork} test_type={config.query.test_type}"
    )
    suites = _resolve_suite_records(config, client)

    all_runs: list[dict[str, Any]] = []
    all_test_stats: list[pd.DataFrame] = []
    trace_per_suite: dict[str, dict[str, Any]] = {}
    runs_per_suite: dict[str, list[dict[str, Any]]] = {}

    window = _window_label(config)
    for suite in suites:
        suite_hash = suite["suite_hash"]
        reporter.info(f"listing runs for suite {suite_hash} {window}")
        runs = client.list_runs(
            suite_hash,
            start_date=_iso(config.query.start_date),
            end_date=_iso(config.query.end_date),
            run_id_pattern=config.query.run_id_pattern,
        )
        reporter.info(f"  → {len(runs)} runs in window")
        for r in runs:
            r.setdefault("suite_hash", suite_hash)
        runs_per_suite[suite_hash] = runs
        all_runs.extend(runs)

    if not all_runs:
        raise EmptyResultError("no runs matched window")

    for suite in suites:
        suite_hash = suite["suite_hash"]
        runs = runs_per_suite[suite_hash]
        if runs:
            run_ids = [r["run_id"] for r in runs]
            stats = client.fetch_test_stats(
                run_ids,
                page_size=config.http.page_size,
                suite_hash=suite_hash,
            )
            all_test_stats.append(stats)
            reporter.info(f"fetching trace summary for suite {suite_hash}")
            trace_per_suite[suite_hash] = client.fetch_trace(suite_hash=suite_hash)

    raw_df = (
        pd.concat(all_test_stats, ignore_index=True)
        if all_test_stats
        else pd.DataFrame()
    )

    reporter.info("parsing fixture titles...")
    parsed_df, unparsed_raw = parse_test_titles(raw_df)
    seen: set[str] = set()
    unparsed: list[str] = []
    for title in unparsed_raw:
        if title not in seen:
            seen.add(title)
            unparsed.append(title)
    merged_trace = _merge_trace_dicts(trace_per_suite)
    trace_frame = _trace_dict_to_frame(merged_trace)

    bench_df = add_opcount(parsed_df, trace_frame, fork=config.query.fork)
    trace_df_out = _build_trace_output(merged_trace, bench_df)
    opcounts_with_opcount = _opcounts_with_opcount(merged_trace, bench_df)

    meta = _build_meta(config, suites, all_runs, unparsed)

    if unparsed:
        _emit_unparsed_warning(unparsed)

    return FetchResult(
        bench_df=bench_df,
        trace_df=trace_df_out,
        opcounts=opcounts_with_opcount,
        meta=meta,
        output_flags={
            "estimator_inputs": config.output.estimator_inputs,
            "merged_parquet": config.output.merged_parquet,
            "trace_parquet": config.output.trace_parquet,
        },
    )


def _resolve_suite_records(
    config: FetchConfig, client: BenchmarkoorClient
) -> list[dict[str, Any]]:
    """Resolve the list of suite records to fetch.

    Honours explicit `query.suites` (skipping `/suites` discovery entirely)
    and otherwise returns the single latest-indexed match.
    """
    explicit = config.query.suites
    if explicit:
        return [{"suite_hash": h, "name": None, "indexed_at": None} for h in explicit]

    all_matching = client.list_suites(
        network=config.query.network,
        fork=config.query.fork,
        test_type=config.query.test_type,
    )
    if not all_matching:
        raise EmptyResultError(
            f"no suites matched network={config.query.network!r} "
            f"fork={config.query.fork!r} test_type={config.query.test_type!r}"
        )
    latest = max(all_matching, key=lambda s: s.get("indexed_at", ""))
    client._fork = config.query.fork
    return [
        {
            "suite_hash": latest["suite_hash"],
            "name": latest.get("name"),
            "indexed_at": latest.get("indexed_at"),
        }
    ]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _window_label(config: FetchConfig) -> str:
    start = _iso(config.query.start_date)
    end = _iso(config.query.end_date)
    if start is None and end is None:
        return "(no date window)"
    return f"(start={start or '-'}, end={end or '-'})"


def _merge_trace_dicts(
    per_suite: dict[str, dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Combine the trace dicts of every fetched suite into one mapping."""
    merged: dict[str, dict[str, float]] = {}
    for trace in per_suite.values():
        if not isinstance(trace, dict):
            continue
        for title, counts in trace.items():
            if not isinstance(counts, dict):
                continue
            merged[title] = {str(k): float(v) for k, v in counts.items()}
    return merged


def _trace_dict_to_frame(trace: dict[str, dict[str, float]]) -> pd.DataFrame:
    if not trace:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(trace, orient="index").fillna(0)
    df.index.name = "test_title"
    return df


def _opcounts_with_opcount(
    trace: dict[str, dict[str, float]], bench_df: pd.DataFrame
) -> dict[str, dict[str, float]]:
    """Return a copy of the trace mapping with each title's `opcount` injected."""
    if bench_df.empty or "test_title" not in bench_df.columns:
        return {title: dict(counts) for title, counts in trace.items()}

    title_to_opcount: dict[str, float] = {}
    for title, opcount in zip(bench_df["test_title"], bench_df["opcount"], strict=True):
        if pd.isna(opcount):
            continue
        title_to_opcount[str(title)] = float(opcount)

    out: dict[str, dict[str, float]] = {}
    for title, counts in trace.items():
        entry = {k: float(v) for k, v in counts.items()}
        if title in title_to_opcount:
            entry["opcount"] = title_to_opcount[title]
        out[title] = entry
    return out


def _build_trace_output(
    trace: dict[str, dict[str, float]], bench_df: pd.DataFrame
) -> pd.DataFrame:
    """Build the `trace.parquet` output: test_title, opcount, then opcode columns."""
    if not trace:
        return pd.DataFrame(columns=["test_title", "opcount"])

    title_to_opcount: dict[str, float] = {}
    if not bench_df.empty and "test_title" in bench_df.columns:
        for title, opcount in zip(
            bench_df["test_title"], bench_df["opcount"], strict=True
        ):
            if pd.isna(opcount):
                continue
            title_to_opcount[str(title)] = float(opcount)

    rows: list[dict[str, Any]] = []
    for title, counts in trace.items():
        row: dict[str, Any] = {"test_title": title}
        if title in title_to_opcount:
            row["opcount"] = title_to_opcount[title]
        else:
            row["opcount"] = None
        for op, count in counts.items():
            row[op] = count
        rows.append(row)
    out = pd.DataFrame(rows)
    cols = ["test_title", "opcount"] + [
        c for c in out.columns if c not in {"test_title", "opcount"}
    ]
    return out[cols]


def _build_meta(
    config: FetchConfig,
    suites: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    unparsed: list[str],
) -> dict[str, Any]:
    """Compose the meta.json payload for a run."""
    from benchmarkoor_fetch import __version__ as package_version

    start_tses = sorted(r["start_ts"] for r in runs if r.get("start_ts"))
    earliest = start_tses[0] if start_tses else None
    latest = start_tses[-1] if start_tses else None

    return {
        "suites": [
            {
                "suite_hash": s["suite_hash"],
                "name": s.get("name"),
                "indexed_at": s.get("indexed_at"),
            }
            for s in suites
        ],
        "query": {
            "network": config.query.network,
            "fork": config.query.fork,
            "test_type": config.query.test_type,
            "start_date": _iso(config.query.start_date),
            "end_date": _iso(config.query.end_date),
            "run_id_pattern": config.query.run_id_pattern,
        },
        "data_window": {"start": earliest, "end": latest},
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "package_version": package_version,
        "unparsed_fixtures": list(unparsed),
    }


def _emit_unparsed_warning(unparsed: list[str]) -> None:
    """Print a single end-of-run warning to stderr with the total count."""
    print(f"WARN: {len(unparsed)} unparsed fixtures", file=sys.stderr)


__all__ = ["EmptyResultError", "run_pipeline"]
