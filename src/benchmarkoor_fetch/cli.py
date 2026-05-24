from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import requests
from pydantic import ValidationError

from benchmarkoor_fetch._reporter import Reporter
from benchmarkoor_fetch.config import FetchConfig
from benchmarkoor_fetch.pipeline import EmptyResultError, run_pipeline

_EXIT_OK = 0
_EXIT_INPUT = 1
_EXIT_HTTP = 2
_EXIT_EMPTY = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser with `run` and `suites` subcommands."""
    parser = argparse.ArgumentParser(
        prog="benchmarkoor-fetch",
        description=(
            "Fetch EVM benchmark suites from the Benchmarkoor API and write "
            "ready-to-analyse tabular outputs."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Fetch + parse + write the artifact bundle."
    )
    run_parser.add_argument("--config", required=True, help="Path to the YAML config.")
    run_parser.add_argument(
        "--out",
        default=None,
        help=("Output directory. Defaults to ./{earliest_run_ts}_{latest_run_ts}/."),
    )
    run_parser.add_argument(
        "--token", default=None, help="Bearer token (overrides env)."
    )
    run_parser.add_argument(
        "--cache-dir", default=None, help="Override the disk-cache directory."
    )
    run_parser.add_argument(
        "--no-cache", action="store_true", help="Bypass cache reads and writes."
    )
    verbosity = run_parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--verbose",
        action="store_true",
        help="Emit per-event detail (cache hit/miss, per-run fetch lines).",
    )
    verbosity.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress milestones and progress bar; only warnings/errors are shown.",
    )
    run_parser.add_argument("--network", default=None, help="Override query.network.")
    run_parser.add_argument("--fork", default=None, help="Override query.fork.")
    run_parser.add_argument(
        "--test-type", default=None, help="Override query.test_type."
    )
    run_parser.add_argument(
        "--start-date", default=None, help="Override query.start_date."
    )
    run_parser.add_argument("--end-date", default=None, help="Override query.end_date.")
    run_parser.add_argument(
        "--no-estimator-inputs",
        action="store_true",
        help="Skip writing runtimes.csv + opcounts.json.",
    )
    run_parser.add_argument(
        "--no-merged-parquet",
        action="store_true",
        help="Skip writing bench_data.parquet.",
    )
    run_parser.add_argument(
        "--no-trace-parquet",
        action="store_true",
        help="Skip writing trace.parquet.",
    )

    suites_parser = subparsers.add_parser(
        "suites", help="Resolve and print the latest matching suite_hash."
    )
    suites_parser.add_argument("--network", required=True)
    suites_parser.add_argument("--fork", required=True)
    suites_parser.add_argument("--test-type", required=True)
    suites_parser.add_argument("--token", default=None)

    return parser


def config_from_args(args: argparse.Namespace) -> FetchConfig:
    """Load the YAML config and apply CLI overrides."""
    config_path = Path(args.config)
    # `fork` is the only required query field; allow YAML to omit it when the
    # CLI supplies one. `network` / `test_type` are optional in the schema
    # (they may be omitted whenever `suites` is set), so no deferral needed.
    needs_partial = getattr(args, "fork", None) is not None
    config = FetchConfig.from_yaml(config_path, allow_partial=needs_partial)

    overrides: dict[str, Any] = {
        "network": args.network,
        "fork": args.fork,
        "test_type": args.test_type,
        "start_date": args.start_date,
        "end_date": args.end_date,
    }
    if getattr(args, "cache_dir", None) is not None:
        overrides["cache_dir"] = Path(args.cache_dir)
    return config.with_cli_overrides(**overrides)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exits via `SystemExit` with one of:

    Exit codes:
      0 - success
      1 - config / input error
      2 - HTTP error
      3 - empty result
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        code = _cmd_run(args)
    elif args.command == "suites":
        code = _cmd_suites(args)
    else:
        parser.print_help(sys.stderr)
        code = _EXIT_INPUT

    raise SystemExit(code)


def _cmd_run(args: argparse.Namespace) -> int:
    from benchmarkoor_fetch.client import BenchmarkoorClient

    try:
        config = config_from_args(args)
    except ValidationError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return _EXIT_INPUT
    except FileNotFoundError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return _EXIT_INPUT

    if args.no_estimator_inputs:
        config = _override_output(config, estimator_inputs=False)
    if args.no_merged_parquet:
        config = _override_output(config, merged_parquet=False)
    if args.no_trace_parquet:
        config = _override_output(config, trace_parquet=False)

    cache_enabled = config.cache.enabled and not args.no_cache
    if args.cache_dir is not None:
        cache_dir: Path | None = Path(args.cache_dir)
    elif cache_enabled:
        cache_dir = config.cache.dir
    else:
        cache_dir = None

    if args.quiet:
        level: str = "quiet"
    elif args.verbose:
        level = "verbose"
    else:
        level = "info"
    reporter = Reporter(level=level)  # type: ignore[arg-type]

    try:
        client = BenchmarkoorClient(
            token=args.token,
            cache_dir=cache_dir,
            max_workers=config.http.max_workers,
            page_size=config.http.page_size,
            retries=config.http.retries,
            backoff_factor=config.http.backoff_factor,
            retry_status=tuple(config.http.retry_status),
            cache_enabled=cache_enabled,
            reporter=reporter,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT

    try:
        result = run_pipeline(config, client=client)
    except EmptyResultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_EMPTY
    except requests.exceptions.RetryError as exc:
        print(f"HTTP error after retries: {exc}", file=sys.stderr)
        return _EXIT_HTTP
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return _EXIT_HTTP
    except requests.RequestException as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return _EXIT_HTTP

    out_dir = (
        Path(args.out)
        if args.out is not None
        else Path.cwd() / result.default_out_dirname()
    )
    result.write(out_dir)
    return _EXIT_OK


def _cmd_suites(args: argparse.Namespace) -> int:
    from benchmarkoor_fetch.client import BenchmarkoorClient

    try:
        client = BenchmarkoorClient(token=args.token)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _EXIT_INPUT

    try:
        suites = client.list_suites(
            network=args.network,
            fork=args.fork.lower(),
            test_type=args.test_type,
        )
    except requests.RequestException as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return _EXIT_HTTP

    if not suites:
        print(
            f"no suites matched network={args.network!r} fork={args.fork!r} "
            f"test_type={args.test_type!r}",
            file=sys.stderr,
        )
        return _EXIT_EMPTY

    latest = max(suites, key=lambda s: s.get("indexed_at", ""))
    print(f"{latest['suite_hash']}\t{latest.get('indexed_at', '')}")
    return _EXIT_OK


def _override_output(config: FetchConfig, **flags: bool) -> FetchConfig:
    """Return a new FetchConfig with output flags overridden."""
    merged = {
        "query": config.query.model_dump(),
        "http": config.http.model_dump(),
        "output": {**config.output.model_dump(), **flags},
        "cache": config.cache.model_dump(),
    }
    return FetchConfig.model_validate(merged)


if __name__ == "__main__":
    main()
