from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator
from typing import Literal, TypeVar

from tqdm import tqdm

T = TypeVar("T")

Level = Literal["quiet", "info", "verbose"]
_LEVELS: tuple[Level, ...] = ("quiet", "info", "verbose")


class Reporter:
    """Single point where the package speaks to the user.

    Three verbosity levels:

      * ``quiet``   — only warnings and errors (which bypass the reporter).
      * ``info``    — high-level milestones plus a tqdm progress bar.
      * ``verbose`` — every milestone plus per-event detail (cache hits,
        per-run fetch announcements).

    All output goes to ``sys.stderr``; the reporter resolves the stream
    lazily on each call so that pytest's ``capsys`` (which swaps
    ``sys.stderr`` per test) can intercept it.
    """

    def __init__(self, level: Level = "info") -> None:
        if level not in _LEVELS:
            raise ValueError(
                f"invalid reporter level {level!r}; expected one of {_LEVELS}"
            )
        self.level: Level = level

    def info(self, msg: str) -> None:
        """Print a milestone (visible at info and verbose)."""
        if self.level == "quiet":
            return
        print(msg, file=sys.stderr)

    def detail(self, msg: str) -> None:
        """Print a per-event detail line (visible only at verbose)."""
        if self.level != "verbose":
            return
        print(msg, file=sys.stderr)

    def progress(self, iterable: Iterable[T], *, total: int, desc: str) -> Iterator[T]:
        """Yield items, drawing a tqdm progress bar at info/verbose levels.

        At ``quiet`` the iterable passes through unchanged so callers do not
        need to branch.
        """
        if self.level == "quiet":
            yield from iterable
            return
        yield from tqdm(iterable, total=total, desc=desc, file=sys.stderr)


__all__ = ["Reporter", "Level"]
