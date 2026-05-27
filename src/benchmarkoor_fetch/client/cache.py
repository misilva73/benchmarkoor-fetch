from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pandas as pd

from benchmarkoor_fetch._reporter import Reporter

T = TypeVar("T")


class DiskCache:
    """Content-addressed disk cache for the Benchmarkoor read path.

    Keys are derived from inputs that fully determine the response, so a hit
    is guaranteed to return identical bytes. Suite discovery and run
    listings are deliberately not cached; see implementation_plan.md §9.
    """

    def __init__(
        self,
        *,
        root: Path,
        enabled: bool = True,
        verbose: bool = False,
        reporter: Reporter | None = None,
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled
        if reporter is not None:
            self.reporter = reporter
        else:
            self.reporter = Reporter(level="verbose" if verbose else "info")

    def test_stats_key(self, *, suite_hash: str, run_id: str) -> Path:
        return self.root / suite_hash / "test_stats" / f"{run_id}.parquet"

    def summary_key(self, *, suite_hash: str) -> Path:
        return self.root / suite_hash / "summary.json"

    def _announce_hit(self, key: Path) -> None:
        self.reporter.detail(f"hit: {key}")

    def _announce_miss(self, key: Path) -> None:
        self.reporter.detail(f"miss: {key}")

    def get_or_fetch_json(self, key: Path, fetcher: Callable[[], T]) -> T:
        """Read JSON from `key` on hit; otherwise call `fetcher` and write."""
        if self.enabled and key.exists():
            self._announce_hit(key)
            return json.loads(key.read_text())
        self._announce_miss(key)
        value = fetcher()
        if self.enabled:
            key.parent.mkdir(parents=True, exist_ok=True)
            key.write_text(json.dumps(value))
        return value

    def get_or_fetch_parquet(
        self,
        key: Path,
        fetcher: Callable[[], pd.DataFrame],
    ) -> pd.DataFrame:
        """Read a parquet DataFrame from `key` on hit; otherwise fetch and write."""
        if self.enabled and key.exists():
            self._announce_hit(key)
            return pd.read_parquet(key)
        self._announce_miss(key)
        frame = fetcher()
        if self.enabled:
            key.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(key, index=False)
        return frame
