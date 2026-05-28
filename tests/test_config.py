"""Unit tests for `benchmarkoor_fetch.config.FetchConfig`.

Covers the YAML loader, defaults, validation rules, CLI override merge, and
the lowercase-fork normalisation rule. Network is never touched here.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import ValidationError

from benchmarkoor_fetch import FetchConfig

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _write_yaml(tmp_path: Path, body: str) -> Path:
    """Write `body` to a temp YAML file and return its path."""
    path = tmp_path / "fetch.yaml"
    path.write_text(dedent(body))
    return path


FULL_YAML = """\
query:
  network: jochemnet
  fork: amsterdam
  test_type: compute
  start_date: "2026-05-18"
  end_date: "2026-05-20"
  run_id_pattern: '.*-full'
  suites:
    - 0xaaa111
    - 0xbbb222

http:
  page_size: 5000
  max_workers: 8
  retries: 4
  backoff_factor: 3
  retry_status: [502, 503]

output:
  estimator_inputs: true
  merged_parquet: false
  trace_parquet: true

cache:
  enabled: false
  dir: /tmp/bench-cache
"""

MINIMAL_YAML = """\
query:
  network: jochemnet
  fork: amsterdam
  test_type: compute
"""


# --------------------------------------------------------------------------- #
# Scenario #1: full valid YAML loads
# --------------------------------------------------------------------------- #


def test_full_valid_yaml_loads(tmp_path: Path) -> None:
    """Scenario #1: full valid YAML loads to FetchConfig with every field set."""
    path = _write_yaml(tmp_path, FULL_YAML)
    config = FetchConfig.from_yaml(path)

    assert config.query.network == "jochemnet"
    assert config.query.fork == "amsterdam"
    assert config.query.test_type == "compute"
    assert str(config.query.start_date) == "2026-05-18"
    assert str(config.query.end_date) == "2026-05-20"
    assert config.query.run_id_pattern == ".*-full"
    assert list(config.query.suites) == ["0xaaa111", "0xbbb222"]

    assert config.http.page_size == 5000
    assert config.http.max_workers == 8
    assert config.http.retries == 4
    assert config.http.backoff_factor == 3
    assert config.http.retry_status == [502, 503]

    assert config.output.estimator_inputs is True
    assert config.output.merged_parquet is False
    assert config.output.trace_parquet is True

    assert config.cache.enabled is False
    assert Path(config.cache.dir) == Path("/tmp/bench-cache")


# --------------------------------------------------------------------------- #
# Scenario #2: missing required query fields
# --------------------------------------------------------------------------- #


def test_missing_fork_raises(tmp_path: Path) -> None:
    """Scenario #2a: `fork` is always required; missing it raises ValidationError."""
    body = "query:\n  network: jochemnet\n  test_type: compute\n"
    path = _write_yaml(tmp_path, body)

    with pytest.raises(ValidationError) as excinfo:
        FetchConfig.from_yaml(path)

    assert "fork" in str(excinfo.value)


@pytest.mark.parametrize("missing_field", ["network", "test_type"])
def test_missing_discovery_field_without_suites_raises(
    tmp_path: Path, missing_field: str
) -> None:
    """Scenario #2b: omitting `network` or `test_type` without `suites` fails.

    Both fields are needed to discover the suite_hash; the alternative is to
    provide an explicit `suites:` list (covered by the next scenario).
    """
    fields = {"network": "jochemnet", "fork": "amsterdam", "test_type": "compute"}
    fields.pop(missing_field)
    body = "query:\n" + "".join(f"  {k}: {v}\n" for k, v in fields.items())
    path = _write_yaml(tmp_path, body)

    with pytest.raises(ValidationError) as excinfo:
        FetchConfig.from_yaml(path)

    message = str(excinfo.value).lower()
    assert "suites" in message
    assert "network" in message and "test_type" in message


@pytest.mark.parametrize(
    "omitted",
    [
        ("network",),
        ("test_type",),
        ("network", "test_type"),
    ],
)
def test_suites_satisfies_discovery_requirement(
    tmp_path: Path, omitted: tuple[str, ...]
) -> None:
    """Scenario #2c: an explicit `suites:` list lets the user skip network/test_type."""
    fields = {"network": "jochemnet", "fork": "amsterdam", "test_type": "compute"}
    for f in omitted:
        fields.pop(f)
    body = (
        "query:\n"
        + "".join(f"  {k}: {v}\n" for k, v in fields.items())
        + "  suites:\n    - 0xaaa111\n"
    )
    path = _write_yaml(tmp_path, body)

    config = FetchConfig.from_yaml(path)
    assert config.query.fork == "amsterdam"
    assert list(config.query.suites) == ["0xaaa111"]
    for f in omitted:
        assert getattr(config.query, f) is None


# --------------------------------------------------------------------------- #
# Scenario #5: start_date alone is allowed
# --------------------------------------------------------------------------- #


def test_start_date_alone_allowed(tmp_path: Path) -> None:
    """Scenario #5: start_date set, end_date omitted → loads cleanly."""
    body = MINIMAL_YAML + '  start_date: "2026-05-18"\n'
    path = _write_yaml(tmp_path, body)
    config = FetchConfig.from_yaml(path)
    assert config.query.start_date is not None
    assert config.query.end_date is None


# --------------------------------------------------------------------------- #
# Scenario #6: end_date alone is allowed
# --------------------------------------------------------------------------- #


def test_end_date_alone_allowed(tmp_path: Path) -> None:
    """Scenario #6: end_date set, start_date omitted → loads cleanly."""
    body = MINIMAL_YAML + '  end_date: "2026-05-20"\n'
    path = _write_yaml(tmp_path, body)
    config = FetchConfig.from_yaml(path)
    assert config.query.end_date is not None
    assert config.query.start_date is None


# --------------------------------------------------------------------------- #
# Scenario #7: inverted date window rejected
# --------------------------------------------------------------------------- #


def test_inverted_date_window_rejected(tmp_path: Path) -> None:
    """Scenario #7: start_date > end_date is rejected with a clear error."""
    body = MINIMAL_YAML + '  start_date: "2026-05-20"\n' + '  end_date: "2026-05-18"\n'
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValidationError) as excinfo:
        FetchConfig.from_yaml(path)
    message = str(excinfo.value).lower()
    assert "start" in message and "end" in message


# --------------------------------------------------------------------------- #
# Scenario #8: invalid ISO date rejected
# --------------------------------------------------------------------------- #


def test_invalid_iso_date_rejected(tmp_path: Path) -> None:
    """Scenario #8: malformed ISO date → error names the field and the bad value."""
    body = MINIMAL_YAML + '  start_date: "not-a-date"\n'
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValidationError) as excinfo:
        FetchConfig.from_yaml(path)
    text = str(excinfo.value)
    assert "start_date" in text
    assert "not-a-date" in text


# --------------------------------------------------------------------------- #
# Scenario #9: token cannot live in YAML
# --------------------------------------------------------------------------- #


def test_token_in_yaml_rejected(tmp_path: Path) -> None:
    """Scenario #9: any `token:` key in YAML is rejected with an auth-source hint."""
    body = MINIMAL_YAML + 'token: "secret-bearer"\n'
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValidationError) as excinfo:
        FetchConfig.from_yaml(path)
    msg = str(excinfo.value).lower()
    assert "token" in msg
    assert "env" in msg or "benchmarkoor_token" in msg or "--token" in msg


def test_token_inside_query_block_also_rejected(tmp_path: Path) -> None:
    """Scenario #9 (nested): token nested under query is also rejected."""
    body = MINIMAL_YAML + '  token: "secret-bearer"\n'
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValidationError):
        FetchConfig.from_yaml(path)


# --------------------------------------------------------------------------- #
# Scenario #10: http defaults populated
# --------------------------------------------------------------------------- #


def test_http_defaults_populated(tmp_path: Path) -> None:
    """Scenario #10: omitted http section gets the documented defaults."""
    path = _write_yaml(tmp_path, MINIMAL_YAML)
    config = FetchConfig.from_yaml(path)

    assert config.http.page_size == 10000
    assert config.http.max_workers == 5
    assert config.http.retries == 3
    assert config.http.backoff_factor == 2
    assert config.http.retry_status == [502, 503, 524]


# --------------------------------------------------------------------------- #
# Scenario #11: output defaults populated
# --------------------------------------------------------------------------- #


def test_output_defaults_populated(tmp_path: Path) -> None:
    """Scenario #11: omitted output section → all three flags default True."""
    path = _write_yaml(tmp_path, MINIMAL_YAML)
    config = FetchConfig.from_yaml(path)

    assert config.output.estimator_inputs is True
    assert config.output.merged_parquet is True
    assert config.output.trace_parquet is True


# --------------------------------------------------------------------------- #
# Scenario #12: cache defaults populated
# --------------------------------------------------------------------------- #


def test_cache_defaults_populated(tmp_path: Path) -> None:
    """Scenario #12: omitted cache section → enabled=True, default dir."""
    path = _write_yaml(tmp_path, MINIMAL_YAML)
    config = FetchConfig.from_yaml(path)

    assert config.cache.enabled is True
    assert Path(config.cache.dir) == Path(".cache/benchmarkoor-fetch")


# --------------------------------------------------------------------------- #
# Scenario #13: CLI overrides applied
# --------------------------------------------------------------------------- #


def test_cli_overrides_applied(tmp_path: Path) -> None:
    """Scenario #13: with_cli_overrides replaces named fields, others untouched."""
    path = _write_yaml(tmp_path, FULL_YAML)
    config = FetchConfig.from_yaml(path)
    overridden = config.with_cli_overrides(fork="osaka", start_date="2026-05-01")

    assert overridden.query.fork == "osaka"
    assert str(overridden.query.start_date) == "2026-05-01"
    # untouched fields keep their YAML values
    assert overridden.query.network == "jochemnet"
    assert overridden.query.test_type == "compute"
    assert overridden.http.page_size == 5000
    # original config is not mutated
    assert config.query.fork == "amsterdam"


# --------------------------------------------------------------------------- #
# Scenario #14: override completes a missing required field
# --------------------------------------------------------------------------- #


def test_override_provides_missing_required_field(tmp_path: Path) -> None:
    """Scenario #14: YAML may omit `fork` if CLI override supplies it.

    The flow is: load partial YAML deferred, apply overrides, validate. This
    test asserts the combined result is a valid `FetchConfig`.
    """
    body = "query:\n  network: jochemnet\n  test_type: compute\n"
    path = _write_yaml(tmp_path, body)
    # The exact API name may be `from_yaml_partial` or a kwarg on `from_yaml`;
    # the contract is: loading then immediately overriding `fork` must succeed.
    partial = FetchConfig.from_yaml(path, allow_partial=True)
    config = partial.with_cli_overrides(fork="osaka")
    assert config.query.fork == "osaka"


# --------------------------------------------------------------------------- #
# Scenario #15: explicit suites list parses to a sequence of strings
# --------------------------------------------------------------------------- #


def test_explicit_suites_list_roundtrips(tmp_path: Path) -> None:
    """Scenario #15: query.suites round-trips as a sequence of strings."""
    path = _write_yaml(tmp_path, FULL_YAML)
    config = FetchConfig.from_yaml(path)
    assert list(config.query.suites) == ["0xaaa111", "0xbbb222"]
    assert all(isinstance(s, str) for s in config.query.suites)


# --------------------------------------------------------------------------- #
# Scenario #16: unknown top-level YAML key rejected
# --------------------------------------------------------------------------- #


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    """Scenario #16: typo'd top-level key (`chache:`) → ValidationError."""
    body = MINIMAL_YAML + "chache:\n  enabled: false\n"
    path = _write_yaml(tmp_path, body)
    with pytest.raises(ValidationError) as excinfo:
        FetchConfig.from_yaml(path)
    assert "chache" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Scenario #16a: query.fork lowercased on every surface
# --------------------------------------------------------------------------- #


def test_fork_lowercased_via_yaml(tmp_path: Path) -> None:
    """Scenario #16a: YAML `fork: Amsterdam` → config.query.fork == 'amsterdam'."""
    body = "query:\n  network: jochemnet\n  fork: Amsterdam\n  test_type: compute\n"
    path = _write_yaml(tmp_path, body)
    config = FetchConfig.from_yaml(path)
    assert config.query.fork == "amsterdam"


def test_fork_lowercased_via_cli_override(tmp_path: Path) -> None:
    """Scenario #16a: CLI override `fork='OSAKA'` → stored lowercased."""
    path = _write_yaml(tmp_path, MINIMAL_YAML)
    config = FetchConfig.from_yaml(path).with_cli_overrides(fork="OSAKA")
    assert config.query.fork == "osaka"


def test_fork_lowercased_via_direct_construction() -> None:
    """Scenario #16a: constructing FetchConfig directly lowercases mixed-case fork."""
    config = FetchConfig(
        query={"network": "jochemnet", "fork": "Prague", "test_type": "compute"},
    )
    assert config.query.fork == "prague"
