"""Public API for benchmarkoor-fetch."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from benchmarkoor_fetch.client import BenchmarkoorClient
from benchmarkoor_fetch.config import FetchConfig
from benchmarkoor_fetch.parse.titles import parse_test_titles
from benchmarkoor_fetch.result import FetchResult

try:
    __version__ = version("benchmarkoor-fetch")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "BenchmarkoorClient",
    "FetchConfig",
    "FetchResult",
    "__version__",
    "parse_test_titles",
]
