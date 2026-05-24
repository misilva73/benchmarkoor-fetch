"""Unit tests for `benchmarkoor_fetch._reporter`.

The reporter is the single point where the package speaks to the user. It
exposes three primitives:

  * `info(msg)` — high-level milestone; shown at `info` and `verbose` levels.
  * `detail(msg)` — per-event detail; shown only at `verbose`.
  * `progress(iterable, *, total, desc)` — wraps with tqdm at `info`/`verbose`,
    yields items unchanged at `quiet`.

All output goes to stderr.
"""

from __future__ import annotations

import sys

import pytest

from benchmarkoor_fetch._reporter import Reporter

# --------------------------------------------------------------------------- #
# Scenario R1: quiet level silences info and detail
# --------------------------------------------------------------------------- #


def test_quiet_level_silences_info_and_detail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter(level="quiet")
    reporter.info("milestone happened")
    reporter.detail("event happened")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# --------------------------------------------------------------------------- #
# Scenario R2: info level writes info to stderr but silences detail
# --------------------------------------------------------------------------- #


def test_info_level_writes_info_silences_detail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter(level="info")
    reporter.info("milestone happened")
    reporter.detail("event happened")
    captured = capsys.readouterr()
    assert "milestone happened" in captured.err
    assert "event happened" not in captured.err
    assert captured.out == ""


# --------------------------------------------------------------------------- #
# Scenario R3: verbose level writes both info and detail to stderr
# --------------------------------------------------------------------------- #


def test_verbose_level_writes_info_and_detail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter(level="verbose")
    reporter.info("milestone happened")
    reporter.detail("event happened")
    captured = capsys.readouterr()
    assert "milestone happened" in captured.err
    assert "event happened" in captured.err
    assert captured.out == ""


# --------------------------------------------------------------------------- #
# Scenario R4: progress yields every item at every level
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("level", ["quiet", "info", "verbose"])
def test_progress_yields_all_items(level: str) -> None:
    reporter = Reporter(level=level)
    items = [1, 2, 3, 4, 5]
    seen = list(reporter.progress(items, total=len(items), desc="work"))
    assert seen == items


# --------------------------------------------------------------------------- #
# Scenario R5: progress draws a bar to stderr at info / verbose levels
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("level", ["info", "verbose"])
def test_progress_writes_bar_to_stderr(
    level: str, capsys: pytest.CaptureFixture[str]
) -> None:
    reporter = Reporter(level=level)
    for _ in reporter.progress(range(3), total=3, desc="work"):
        pass
    captured = capsys.readouterr()
    # tqdm emits the desc and a counter like `3/3`; both should appear on stderr.
    assert "work" in captured.err, captured
    assert "3/3" in captured.err, captured
    assert captured.out == ""


# --------------------------------------------------------------------------- #
# Scenario R6: progress is silent at quiet level
# --------------------------------------------------------------------------- #


def test_progress_silent_at_quiet_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = Reporter(level="quiet")
    for _ in reporter.progress(range(3), total=3, desc="work"):
        pass
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# --------------------------------------------------------------------------- #
# Scenario R7: info goes to stderr, never stdout
# --------------------------------------------------------------------------- #


def test_info_does_not_write_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    reporter = Reporter(level="verbose")
    reporter.info("hello")
    reporter.detail("world")
    captured = capsys.readouterr()
    assert "hello" not in captured.out
    assert "world" not in captured.out


# --------------------------------------------------------------------------- #
# Scenario R8: invalid level rejected at construction
# --------------------------------------------------------------------------- #


def test_invalid_level_raises() -> None:
    with pytest.raises(ValueError):
        Reporter(level="loud")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Scenario R9: default level is info
# --------------------------------------------------------------------------- #


def test_default_level_is_info(capsys: pytest.CaptureFixture[str]) -> None:
    reporter = Reporter()
    reporter.info("hi")
    reporter.detail("hidden")
    captured = capsys.readouterr()
    assert "hi" in captured.err
    assert "hidden" not in captured.err


# --------------------------------------------------------------------------- #
# Scenario R10: stderr capture survives even when tqdm grabs sys.stderr early
# --------------------------------------------------------------------------- #


def test_reporter_uses_current_sys_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The reporter must resolve sys.stderr at call time, not at construction.

    Otherwise pytest's capsys (which swaps sys.stderr per test) cannot see
    reporter output written from inside a long-lived client.
    """
    reporter = Reporter(level="info")
    # Sanity: writing directly to sys.stderr lands in the captured buffer.
    print("baseline", file=sys.stderr)
    reporter.info("from reporter")
    captured = capsys.readouterr()
    assert "baseline" in captured.err
    assert "from reporter" in captured.err
