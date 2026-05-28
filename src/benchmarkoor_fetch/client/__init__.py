from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import requests

from benchmarkoor_fetch._reporter import Reporter
from benchmarkoor_fetch.client import (
    runs as runs_module,
)
from benchmarkoor_fetch.client import (
    session as session_module,
)
from benchmarkoor_fetch.client import (
    suites as suites_module,
)
from benchmarkoor_fetch.client import (
    test_stats as test_stats_module,
)
from benchmarkoor_fetch.client import (
    traces as traces_module,
)
from benchmarkoor_fetch.client.cache import DiskCache

if TYPE_CHECKING:
    from benchmarkoor_fetch.config import FetchConfig
    from benchmarkoor_fetch.result import FetchResult


BASE_URL = "https://benchmarkoor-api.core.ethpandaops.io/api/v1/index/query"
FILES_BASE_URL = "https://benchmarkoor-api.core.ethpandaops.io/api/v1/files"
DEFAULT_PAGE_SIZE = 10000
DEFAULT_MAX_WORKERS = 5


class _SimpleHttpConfig:
    """Duck-typed http config used by `build_session` without a full FetchConfig."""

    def __init__(
        self,
        *,
        retries: int = 3,
        backoff_factor: int | float = 2,
        retry_status: tuple[int, ...] = (502, 503, 524),
    ) -> None:
        self.retries = retries
        self.backoff_factor = backoff_factor
        self.retry_status = list(retry_status)


class BenchmarkoorClient:
    """Read-only client for the Benchmarkoor API.

    Token resolution: the `token` kwarg wins over the `BENCHMARKOOR_TOKEN`
    environment variable. Missing both raises `RuntimeError` at construction.

    The client mirrors the four endpoints the package needs (`/suites`,
    `/runs`, `/test_stats`, `/files/.../summary.json`) plus a `parse(...)`
    wrapper that runs the title parser + opcount join in one call.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        cache_dir: Path | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        page_size: int = DEFAULT_PAGE_SIZE,
        retries: int = 3,
        backoff_factor: int | float = 2,
        retry_status: tuple[int, ...] = (502, 503, 524),
        base_url: str = BASE_URL,
        files_base_url: str = FILES_BASE_URL,
        cache_enabled: bool = True,
        verbose: bool = False,
        reporter: Reporter | None = None,
    ) -> None:
        resolved = token if token is not None else os.environ.get("BENCHMARKOOR_TOKEN")
        if not resolved:
            raise RuntimeError(
                "Benchmarkoor bearer token not found: pass `token=...` or set "
                "BENCHMARKOOR_TOKEN in the environment."
            )
        self._token = resolved
        self._max_workers = max_workers
        self._page_size = page_size
        self._base_url = base_url
        self._files_base_url = files_base_url
        if reporter is not None:
            self.reporter = reporter
        else:
            self.reporter = Reporter(level="verbose" if verbose else "info")
        self._fork: str | None = None

        http_cfg = _SimpleHttpConfig(
            retries=retries,
            backoff_factor=backoff_factor,
            retry_status=retry_status,
        )
        self._session = session_module.build_session(http_cfg)
        self._session.headers.update({"Authorization": f"Bearer {resolved}"})

        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self._cache_dir is not None:
            self._cache: DiskCache | None = DiskCache(
                root=self._cache_dir,
                enabled=cache_enabled,
                reporter=self.reporter,
            )
        else:
            self._cache = None

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #

    def resolve_suite(self, *, network: str, fork: str, test_type: str) -> str:
        """Return the `suite_hash` of the latest suite matching the tuple."""
        self._fork = fork
        return suites_module.resolve_suite(
            self._session,
            network=network,
            fork=fork,
            test_type=test_type,
            base_url=self._base_url,
            page_size=self._page_size,
        )

    def list_suites(
        self, *, network: str, fork: str, test_type: str
    ) -> list[dict[str, Any]]:
        """Return all suite entries matching the tuple."""
        return suites_module.list_suites(
            self._session,
            network=network,
            fork=fork,
            test_type=test_type,
            base_url=self._base_url,
            page_size=self._page_size,
        )

    def list_runs(
        self,
        suite_hash: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        run_id_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """List runs for the given suite, optionally narrowed by window.

        `start_date` is applied server-side via the `timestamp=gt.{unix_ts}`
        filter; `end_date` and `run_id_pattern` are applied in-process.
        `run_id_pattern` is a regex matched against each `run_id` with
        `re.fullmatch` — the whole `run_id` must match. This endpoint is
        not cached: new runs accumulate over time so the response is not
        content-addressed (same reasoning as suite discovery).
        """

        def fetcher() -> list[dict[str, Any]]:
            try:
                return runs_module.list_runs(
                    self._session,
                    suite_hash=suite_hash,
                    base_url=self._base_url,
                    start_date=start_date,
                    page_size=self._page_size,
                )
            except requests.HTTPError as exc:
                raise requests.HTTPError(
                    f"{exc} (suite_hash={suite_hash})", response=exc.response
                ) from exc

        raw = fetcher()

        return runs_module.filter_runs(
            raw,
            end_date=str(end_date) if end_date is not None else None,
            run_id_pattern=run_id_pattern,
        )

    def fetch_test_stats(
        self,
        run_ids: list[str] | list[dict[str, Any]],
        *,
        page_size: int | None = None,
        suite_hash: str | None = None,
    ) -> pd.DataFrame:
        """Fetch /test_stats for each run_id; cache per-run when `suite_hash` is given.

        `run_ids` accepts either a list of run_id strings or a list of run
        record dicts (as returned by `list_runs`); dicts have their `run_id`
        key extracted.
        """
        effective_page_size = page_size if page_size is not None else self._page_size
        run_ids = [r["run_id"] if isinstance(r, dict) else r for r in run_ids]

        if not run_ids:
            return test_stats_module.fetch_test_stats(
                self._session,
                run_ids=[],
                page_size=effective_page_size,
                max_workers=self._max_workers,
                base_url=self._base_url,
            )

        if suite_hash is not None:
            progress_desc = f"fetching test_stats (suite {suite_hash[:10]})"
        else:
            progress_desc = "fetching test_stats"

        parts: list[pd.DataFrame] = []
        for run_id in self.reporter.progress(
            run_ids, total=len(run_ids), desc=progress_desc
        ):
            self.reporter.detail(f"run {run_id}: fetching test_stats")
            cache_key = (
                self._cache.test_stats_key(suite_hash=suite_hash, run_id=run_id)
                if self._cache is not None and suite_hash is not None
                else None
            )

            def fetch_raw(rid: str = run_id) -> pd.DataFrame:
                return test_stats_module.fetch_test_stats_for_run_raw(
                    self._session,
                    run_id=rid,
                    page_size=effective_page_size,
                    max_workers=self._max_workers,
                    base_url=self._base_url,
                )

            if cache_key is not None and self._cache is not None:
                raw = self._cache.get_or_fetch_parquet(cache_key, fetch_raw)
            else:
                raw = fetch_raw()
            parts.append(raw)

        combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        normalised = test_stats_module.normalise_columns(combined)
        for col in (
            "run_id",
            "client_name",
            "test_title",
            "test_runtime_ms",
            "ingestion_timestamp",
        ):
            if col not in normalised.columns:
                normalised[col] = pd.Series(dtype=object)
        return normalised

    def fetch_trace(self, suite_hash: str) -> dict[str, Any]:
        """Fetch the per-suite trace summary (cached when a cache_dir is set).

        Returns a `{test_title: {opcode_name: count}}` mapping; the raw API
        payload is cached on disk and transformed on every call.
        """

        def fetcher() -> dict[str, Any]:
            return traces_module.fetch_trace_raw(
                self._session,
                suite_hash=suite_hash,
                files_base_url=self._files_base_url,
            )

        if self._cache is None:
            raw = fetcher()
        else:
            key = self._cache.summary_key(suite_hash=suite_hash)
            raw = self._cache.get_or_fetch_json(key, fetcher)
        return traces_module.transform_trace(raw)

    # ------------------------------------------------------------------ #
    # Style-B parse + Style-A run shim
    # ------------------------------------------------------------------ #

    def parse(
        self,
        raw_df: pd.DataFrame,
        trace_df: pd.DataFrame | dict[str, Any],
        *,
        fork: str | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Run the title parser + opcount join over a raw test_stats frame."""
        from benchmarkoor_fetch.parse.opcount import add_opcount
        from benchmarkoor_fetch.parse.titles import parse_test_titles

        effective_fork = fork or self._fork or "prague"
        parsed, _ = parse_test_titles(raw_df)

        trace_frame = _trace_to_dataframe(trace_df)
        bench_df = add_opcount(parsed, trace_frame, fork=effective_fork)
        return bench_df, trace_frame

    def run(self, config: FetchConfig) -> FetchResult:
        """Execute the full fetch + parse + write pipeline."""
        from benchmarkoor_fetch.pipeline import run_pipeline

        return run_pipeline(config, client=self)


def _trace_to_dataframe(trace: pd.DataFrame | dict[str, Any]) -> pd.DataFrame:
    """Normalise a trace summary (dict or DataFrame) to a DF keyed by test_title."""
    if isinstance(trace, pd.DataFrame):
        return trace
    if not trace:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(trace, orient="index").fillna(0)
    df.index.name = "test_title"
    return df


__all__ = ["BenchmarkoorClient", "BASE_URL"]
