from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pandas as pd

T = TypeVar("T")


class DiskCache:
    """Content-addressed disk cache for the Benchmarkoor read path.

    Keys are derived from inputs that fully determine the response, so a hit
    is guaranteed to return identical bytes. Suite discovery is deliberately
    not cached; see implementation_plan.md §9.
    """

    def __init__(
        self,
        *,
        root: Path,
        enabled: bool = True,
        verbose: bool = False,
    ) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.verbose = verbose

    def runs_key(self, *, suite_hash: str, start_date: str | None = None) -> Path:
        suffix = "all" if start_date is None else f"from-{start_date}"
        return self.root / suite_hash / f"runs-{suffix}.json"

    def test_stats_key(self, *, suite_hash: str, run_id: str) -> Path:
        return self.root / suite_hash / "test_stats" / f"{run_id}.parquet"

    def summary_key(self, *, suite_hash: str) -> Path:
        return self.root / suite_hash / "summary.json"

    def _announce_miss(self, key: Path) -> None:
        if self.verbose:
            print(f"miss: {key}", file=sys.stderr)

    def get_or_fetch_json(self, key: Path, fetcher: Callable[[], T]) -> T:
        """Read JSON from `key` on hit; otherwise call `fetcher` and write."""
        if self.enabled and key.exists():
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
            return pd.read_parquet(key)
        self._announce_miss(key)
        frame = fetcher()
        if self.enabled:
            key.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(key, index=False)
        return frame
