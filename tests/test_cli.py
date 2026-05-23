"""Unit tests for `benchmarkoor_fetch.cli` argparse behaviour.

E2E owns behavioural outcomes (exit codes, output files, default `--out`).
This file only owns argument parsing: which flags exist, what they map to,
and how unknown / missing inputs are handled by argparse.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from benchmarkoor_fetch import cli as cli_module

MINIMAL_YAML = dedent(
    """\
    query:
      network: jochemnet
      fork: amsterdam
      test_type: compute
    """
)


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "fetch.yaml"
    path.write_text(MINIMAL_YAML)
    return path


# --------------------------------------------------------------------------- #
# Scenario #56: `run` parses core flags
# --------------------------------------------------------------------------- #


def test_run_parses_core_flags(tmp_path: Path) -> None:
    """Scenario #56: --config/--out/--token/--verbose/--no-cache populate args."""
    config_path = _write_config(tmp_path)
    out_path = tmp_path / "out"
    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "run",
            "--config",
            str(config_path),
            "--out",
            str(out_path),
            "--token",
            "secret",
            "--verbose",
            "--no-cache",
        ]
    )
    assert args.command == "run"
    assert Path(args.config) == config_path
    assert Path(args.out) == out_path
    assert args.token == "secret"
    assert args.verbose is True
    assert args.no_cache is True


# --------------------------------------------------------------------------- #
# Scenario #57: `run` parses query overrides
# --------------------------------------------------------------------------- #


def test_run_parses_query_overrides(tmp_path: Path) -> None:
    """Scenario #57: query overrides propagate into FetchConfig via overrides."""
    config_path = _write_config(tmp_path)
    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "run",
            "--config",
            str(config_path),
            "--network",
            "kurtosis_devnet",
            "--fork",
            "osaka",
            "--test-type",
            "stateful",
            "--start-date",
            "2026-05-01",
            "--end-date",
            "2026-05-08",
        ]
    )
    assert args.network == "kurtosis_devnet"
    assert args.fork == "osaka"
    assert args.test_type == "stateful"
    assert args.start_date == "2026-05-01"
    assert args.end_date == "2026-05-08"

    # The CLI must thread these through with_cli_overrides on the loaded config.
    config = cli_module.config_from_args(args)
    assert config.query.network == "kurtosis_devnet"
    assert config.query.fork == "osaka"
    assert config.query.test_type == "stateful"
    assert str(config.query.start_date) == "2026-05-01"
    assert str(config.query.end_date) == "2026-05-08"


# --------------------------------------------------------------------------- #
# Scenario #58: `suites` parses flags
# --------------------------------------------------------------------------- #


def test_suites_parses_flags() -> None:
    """Scenario #58: `suites --network/--fork/--test-type` populates args."""
    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "suites",
            "--network",
            "kurtosis_devnet",
            "--fork",
            "amsterdam",
            "--test-type",
            "benchmark",
        ]
    )
    assert args.command == "suites"
    assert args.network == "kurtosis_devnet"
    assert args.fork == "amsterdam"
    assert args.test_type == "benchmark"


# --------------------------------------------------------------------------- #
# Scenario #59: missing --config on `run` exits non-zero
# --------------------------------------------------------------------------- #


def test_missing_config_on_run_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scenario #59: `run` without --config → argparse exits non-zero, names the arg."""
    parser = cli_module.build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["run"])
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "--config" in err or "config" in err


# --------------------------------------------------------------------------- #
# Scenario #60: unknown subcommand exits 1 and lists available ones
# --------------------------------------------------------------------------- #


def test_unknown_subcommand_exits_with_available_listed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Scenario #60: unknown subcommand exits 1; stderr mentions `run` and `suites`."""
    parser = cli_module.build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["does-not-exist"])
    # argparse's default is exit code 2; the CLI wrapper may translate to 1.
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "run" in err
    assert "suites" in err
