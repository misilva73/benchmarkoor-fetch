"""Shared fixtures for the end-to-end test suite.

Mocks the Benchmarkoor HTTP boundary with `responses` and provides helpers
for running the CLI in-process while still exercising the real argparse +
exit-code path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import responses

# Base URL for the Benchmarkoor API. All mocked URLs hang off this.
BENCHMARKOOR_BASE_URL = "https://benchmarkoor-api.core.ethpandaops.io"

# Root of the committed fixture bundle.
E2E_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "e2e"
RESPONSES_DIR = E2E_DATA_DIR / "responses"
VARIANTS_DIR = E2E_DATA_DIR / "variants"
GOLDEN_DIR = E2E_DATA_DIR / "golden_outputs"
CANONICAL_CONFIG = E2E_DATA_DIR / "fetch.yaml"

# The canonical fixture's latest matching suite_hash. Tests that need the
# resolved hash directly can import this.
CANONICAL_SUITE_HASH = (
    "0xabc1230000000000000000000000000000000000000000000000000000000000"
)
CANONICAL_RUN_IDS = ("run-001-full", "run-002-full", "run-003-full")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# pytest CLI option: --regenerate-golden
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--regenerate-golden",
        action="store_true",
        default=False,
        help=(
            "Regenerate committed golden artifacts under "
            "tests/data/e2e/golden_outputs/ from the current pipeline output."
        ),
    )


@pytest.fixture
def regenerate_golden(request: pytest.FixtureRequest) -> bool:
    """True when the test run was invoked with --regenerate-golden."""

    return bool(request.config.getoption("--regenerate-golden"))


# ---------------------------------------------------------------------------
# Directory + env fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """Per-test cache directory under pytest's tmp_path."""

    path = tmp_path / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_out_dir(tmp_path: Path) -> Path:
    """Per-test output directory under pytest's tmp_path."""

    path = tmp_path / "out"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def canonical_config_path() -> Path:
    return CANONICAL_CONFIG


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN_DIR


@pytest.fixture
def bench_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set BENCHMARKOOR_TOKEN for the test and clean up after."""

    token = "test-token-abc"
    monkeypatch.setenv("BENCHMARKOOR_TOKEN", token)
    return token


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure BENCHMARKOOR_TOKEN is not set in the environment."""

    monkeypatch.delenv("BENCHMARKOOR_TOKEN", raising=False)


# ---------------------------------------------------------------------------
# HTTP mocking
# ---------------------------------------------------------------------------


def _register_canonical_suites(rsps: responses.RequestsMock) -> None:
    rsps.add(
        responses.GET,
        f"{BENCHMARKOOR_BASE_URL}/suites",
        json=_load_json(RESPONSES_DIR / "suites.json"),
        status=200,
    )


def _register_canonical_runs(rsps: responses.RequestsMock) -> None:
    rsps.add(
        responses.GET,
        f"{BENCHMARKOOR_BASE_URL}/runs",
        json=_load_json(RESPONSES_DIR / "runs.json"),
        status=200,
    )


def _register_canonical_test_stats(rsps: responses.RequestsMock) -> None:
    """Register three pages of test_stats under the /test_stats endpoint.

    Each run_id will request paginated stats from this URL. The mock matches
    on the `page` query parameter regardless of `run_id`, so all three runs
    receive the same paginated content. With three pages of 4/4/2 rows and
    three runs, the merged DataFrame ends up with 30 rows.
    """

    for page in (1, 2, 3):
        rsps.add(
            responses.GET,
            f"{BENCHMARKOOR_BASE_URL}/test_stats",
            json=_load_json(RESPONSES_DIR / f"test_stats_page{page}.json"),
            status=200,
            match=[
                responses.matchers.query_param_matcher(
                    {"page": str(page)}, strict_match=False
                )
            ],
        )


def _register_canonical_summary(rsps: responses.RequestsMock) -> None:
    rsps.add(
        responses.GET,
        f"{BENCHMARKOOR_BASE_URL}/files/{CANONICAL_SUITE_HASH}/summary.json",
        json=_load_json(RESPONSES_DIR / "summary.json"),
        status=200,
    )


@dataclass
class MockedApi:
    """Wraps a `responses.RequestsMock` plus the fixture-side helpers."""

    rsps: responses.RequestsMock
    base_url: str = BENCHMARKOOR_BASE_URL

    def calls_to(self, url_suffix: str) -> list[responses.Call]:
        """All recorded calls whose URL contains the given suffix."""

        return [c for c in self.rsps.calls if url_suffix in c.request.url]

    def call_count(self, url_suffix: str) -> int:
        return len(self.calls_to(url_suffix))


@pytest.fixture
def mocked_api(bench_token: str) -> Iterator[MockedApi]:
    """RequestsMock pre-populated with the canonical fixture bundle.

    The token fixture also runs so any CLI/library call has a bearer token
    to pick up.
    """

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        _register_canonical_suites(rsps)
        _register_canonical_runs(rsps)
        _register_canonical_test_stats(rsps)
        _register_canonical_summary(rsps)
        yield MockedApi(rsps=rsps)


@pytest.fixture
def mocked_api_raw(bench_token: str) -> Iterator[MockedApi]:
    """Empty RequestsMock — tests register their own endpoints."""

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield MockedApi(rsps=rsps)


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


@dataclass
class RunnerResult:
    exit_code: int
    stdout: str
    stderr: str


@pytest.fixture
def runner() -> Runner:
    """Returns a helper that invokes the CLI in-process via `cli.main()`.

    In-process keeps `responses` patching active (subprocess would not see
    the mocked HTTP layer). Exit codes are captured via `SystemExit`.
    """

    return Runner()


class Runner:
    """Invokes `benchmarkoor_fetch.cli:main` in-process and captures I/O."""

    def invoke(self, *args: str, env: dict[str, str] | None = None) -> RunnerResult:
        # Import lazily so a test that runs before the module exists still
        # imports the module here and surfaces the real ImportError.
        import contextlib
        import io

        from benchmarkoor_fetch import cli

        old_argv = sys.argv
        old_env: dict[str, str | None] = {}
        if env is not None:
            for key, val in env.items():
                old_env[key] = os.environ.get(key)
                os.environ[key] = val

        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = 0
        try:
            sys.argv = ["benchmarkoor-fetch", *args]
            try:
                with (
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    cli.main()
            except SystemExit as exc:
                code = exc.code
                if isinstance(code, int):
                    exit_code = code
                elif code is None:
                    exit_code = 0
                else:
                    # str-typed exit code, treat as non-zero
                    stderr.write(str(code))
                    exit_code = 1
        finally:
            sys.argv = old_argv
            if env is not None:
                for key, old_val in old_env.items():
                    if old_val is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = old_val

        return RunnerResult(
            exit_code=exit_code,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    def invoke_subprocess(
        self, *args: str, env: dict[str, str] | None = None
    ) -> RunnerResult:
        """Last-resort subprocess invocation.

        Subprocess invocations escape the `responses` mock, so most tests
        should prefer `invoke()`. This helper is here only for tests that
        truly want a real process boundary (e.g. confirming the console
        script entry point exists).
        """

        merged_env = os.environ.copy()
        if env is not None:
            merged_env.update(env)
        completed = subprocess.run(
            ["benchmarkoor-fetch", *args],
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
        )
        return RunnerResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


# ---------------------------------------------------------------------------
# Golden-bundle diff helpers
# ---------------------------------------------------------------------------


@dataclass
class GoldenHelper:
    """Compares produced artifacts against the committed golden bundle.

    When the test session was started with `--regenerate-golden`, the
    helper overwrites the golden file instead of asserting equality.
    """

    golden_dir: Path
    regenerate: bool

    # `fetched_at` and `package_version` are stripped before meta.json
    # equality. They are asserted separately in scenario #21.
    META_DYNAMIC_FIELDS: tuple[str, ...] = (
        "fetched_at",
        "package_version",
    )

    def assert_csv(self, produced: Path, golden_name: str) -> None:
        import pandas as pd

        golden = self.golden_dir / golden_name
        produced_df = pd.read_csv(produced)
        if self.regenerate:
            self.golden_dir.mkdir(parents=True, exist_ok=True)
            produced_df.to_csv(golden, index=False)
            return
        assert golden.exists(), f"golden file missing: {golden}"
        expected_df = pd.read_csv(golden)
        produced_sorted = produced_df.sort_values(
            by=list(produced_df.columns)
        ).reset_index(drop=True)
        expected_sorted = expected_df.sort_values(
            by=list(expected_df.columns)
        ).reset_index(drop=True)
        pd.testing.assert_frame_equal(produced_sorted, expected_sorted)

    def assert_parquet(self, produced: Path, golden_name: str) -> None:
        import pandas as pd

        golden = self.golden_dir / golden_name
        produced_df = pd.read_parquet(produced)
        if self.regenerate:
            self.golden_dir.mkdir(parents=True, exist_ok=True)
            produced_df.to_parquet(golden, index=False)
            return
        assert golden.exists(), f"golden file missing: {golden}"
        expected_df = pd.read_parquet(golden)
        produced_sorted = produced_df.sort_values(
            by=list(produced_df.columns)
        ).reset_index(drop=True)
        expected_sorted = expected_df.sort_values(
            by=list(expected_df.columns)
        ).reset_index(drop=True)
        pd.testing.assert_frame_equal(produced_sorted, expected_sorted)

    def assert_json(self, produced: Path, golden_name: str) -> None:
        golden = self.golden_dir / golden_name
        produced_data = json.loads(produced.read_text())
        if self.regenerate:
            self.golden_dir.mkdir(parents=True, exist_ok=True)
            golden.write_text(json.dumps(produced_data, indent=2, sort_keys=True))
            return
        assert golden.exists(), f"golden file missing: {golden}"
        expected_data = json.loads(golden.read_text())
        assert _normalised(produced_data) == _normalised(expected_data)

    def assert_meta(self, produced: Path, golden_name: str) -> None:
        """Like assert_json, but strips dynamic fields before comparing."""

        golden = self.golden_dir / golden_name
        produced_data = json.loads(produced.read_text())
        stripped_produced = {
            k: v for k, v in produced_data.items() if k not in self.META_DYNAMIC_FIELDS
        }
        if self.regenerate:
            self.golden_dir.mkdir(parents=True, exist_ok=True)
            golden.write_text(json.dumps(stripped_produced, indent=2, sort_keys=True))
            return
        assert golden.exists(), f"golden file missing: {golden}"
        expected_data = json.loads(golden.read_text())
        stripped_expected = {
            k: v for k, v in expected_data.items() if k not in self.META_DYNAMIC_FIELDS
        }
        assert _normalised(stripped_produced) == _normalised(stripped_expected)


def _normalised(obj: Any) -> Any:
    """Recursively sort lists so order does not affect equality."""

    if isinstance(obj, dict):
        return {k: _normalised(v) for k, v in obj.items()}
    if isinstance(obj, list):
        normalised = [_normalised(v) for v in obj]
        try:
            return sorted(normalised, key=lambda v: json.dumps(v, sort_keys=True))
        except TypeError:
            return normalised
    return obj


@pytest.fixture
def golden(regenerate_golden: bool) -> GoldenHelper:
    return GoldenHelper(golden_dir=GOLDEN_DIR, regenerate=regenerate_golden)


# ---------------------------------------------------------------------------
# Variant fixture helpers — for tests that need non-canonical responses
# ---------------------------------------------------------------------------


def register_runs(rsps: responses.RequestsMock, body: Any, status: int = 200) -> None:
    rsps.add(
        responses.GET,
        f"{BENCHMARKOOR_BASE_URL}/runs",
        json=body,
        status=status,
    )


def register_suites(rsps: responses.RequestsMock, body: Any, status: int = 200) -> None:
    rsps.add(
        responses.GET,
        f"{BENCHMARKOOR_BASE_URL}/suites",
        json=body,
        status=status,
    )


def register_test_stats(
    rsps: responses.RequestsMock, body: Any, status: int = 200
) -> None:
    rsps.add(
        responses.GET,
        f"{BENCHMARKOOR_BASE_URL}/test_stats",
        json=body,
        status=status,
    )


def register_summary(
    rsps: responses.RequestsMock,
    suite_hash: str,
    body: Any,
    status: int = 200,
) -> None:
    rsps.add(
        responses.GET,
        f"{BENCHMARKOOR_BASE_URL}/files/{suite_hash}/summary.json",
        json=body,
        status=status,
    )


def load_variant(name: str) -> Any:
    """Load a variant JSON by filename (under tests/data/e2e/variants/)."""

    return _load_json(VARIANTS_DIR / name)


def variant_path(name: str) -> Path:
    return VARIANTS_DIR / name
