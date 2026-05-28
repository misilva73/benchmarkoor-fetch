from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError


class _ConfigLoader(yaml.SafeLoader):
    """SafeLoader that preserves hex-prefixed values as strings.

    PyYAML's default int resolver matches `0xaaa111` and coerces it to an
    integer, which mangles suite hashes. This subclass strips the int
    resolver and re-adds a decimal-only one.
    """


_ConfigLoader.yaml_implicit_resolvers = {
    first_char: [
        (tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:int"
    ]
    for first_char, resolvers in _ConfigLoader.yaml_implicit_resolvers.items()
}
_ConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^[-+]?(0|[1-9][0-9_]*)$"),
    list("-+0123456789"),
)


class _QueryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fork: str
    network: str | None = None
    test_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    run_id_pattern: str | None = None
    suites: list[str] | None = None

    @field_validator("fork", mode="before")
    @classmethod
    def _lowercase_fork(cls, v: object) -> str:
        if isinstance(v, str):
            return v.lower()
        return v  # type: ignore[return-value]

    @field_validator("suites", mode="before")
    @classmethod
    def _stringify_suites(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, list):
            return [str(s) for s in v]
        return v

    @field_validator("run_id_pattern")
    @classmethod
    def _compile_run_id_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"run_id_pattern is not a valid regex: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _check_date_window(self) -> _QueryConfig:
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError(
                    f"start_date ({self.start_date}) must not be after "
                    f"end_date ({self.end_date})"
                )
        return self

    @model_validator(mode="after")
    def _check_suites_or_discovery_tuple(self) -> _QueryConfig:
        if not self.suites and (self.network is None or self.test_type is None):
            raise ValueError(
                "query must provide either `suites` or both `network` and "
                "`test_type` (needed to discover the suite_hash)"
            )
        return self


class _PartialQueryConfig(BaseModel):
    """Like `_QueryConfig` but required fields are optional, for `allow_partial`."""

    model_config = ConfigDict(extra="forbid")

    network: str | None = None
    fork: str | None = None
    test_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    run_id_pattern: str | None = None
    suites: list[str] | None = None

    @field_validator("fork", mode="before")
    @classmethod
    def _lowercase_fork(cls, v: object) -> str | None:
        if isinstance(v, str):
            return v.lower()
        return v  # type: ignore[return-value]

    @field_validator("run_id_pattern")
    @classmethod
    def _compile_run_id_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"run_id_pattern is not a valid regex: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _check_date_window(self) -> _PartialQueryConfig:
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError(
                    f"start_date ({self.start_date}) must not be after "
                    f"end_date ({self.end_date})"
                )
        return self


class _HttpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_size: int = 10000
    max_workers: int = 5
    retries: int = 3
    backoff_factor: int | float = 2
    retry_status: list[int] = [502, 503, 524]


class _OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimator_inputs: bool = True
    merged_parquet: bool = True
    trace_parquet: bool = True


class _CacheConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    dir: Path = Path(".cache/benchmarkoor-fetch")

    @field_validator("dir", mode="before")
    @classmethod
    def _expand_home(cls, v: object) -> Path:
        return Path(str(v)).expanduser()


def _raise_token_error(raw: dict) -> None:
    """Raise ValidationError if `token` appears at top level or under `query`."""
    from pydantic import ValidationError as _VE

    errors: list[InitErrorDetails] = []

    if "token" in raw:
        errors.append(
            InitErrorDetails(
                type=PydanticCustomError(
                    "token_in_config",
                    "token must not appear in the config file; "
                    "pass it via the BENCHMARKOOR_TOKEN env var or --token flag",
                ),
                loc=("token",),
                input=raw["token"],
            )
        )

    query_block = raw.get("query", {}) or {}
    if isinstance(query_block, dict) and "token" in query_block:
        errors.append(
            InitErrorDetails(
                type=PydanticCustomError(
                    "token_in_config",
                    "token must not appear in the config file; "
                    "pass it via the BENCHMARKOOR_TOKEN env var or --token flag",
                ),
                loc=("query", "token"),
                input=query_block["token"],
            )
        )

    if errors:
        raise _VE.from_exception_data(
            title="FetchConfig",
            input_type="python",
            line_errors=errors,
        )


class FetchConfig(BaseModel):
    """Configuration for a Benchmarkoor fetch run.

    Load from a YAML file with `from_yaml`; apply CLI overrides with
    `with_cli_overrides`. Auth (bearer token) is never part of the config —
    pass it via `BENCHMARKOOR_TOKEN` env var, `--token` CLI flag, or
    `BenchmarkoorClient(token=...)`.
    """

    model_config = ConfigDict(extra="forbid")

    query: _QueryConfig
    http: _HttpConfig = _HttpConfig()
    output: _OutputConfig = _OutputConfig()
    cache: _CacheConfig = _CacheConfig()

    @classmethod
    def from_yaml(cls, path: Path, *, allow_partial: bool = False) -> FetchConfig:
        """Load a `FetchConfig` from a YAML file.

        Args:
            path: Filesystem path to the YAML config file.
            allow_partial: When True, required query fields (network, fork,
                test_type) may be absent; validation is deferred until
                `with_cli_overrides` is called with the missing values.

        Returns:
            A validated `FetchConfig` instance.

        Raises:
            pydantic.ValidationError: If the YAML content violates any
                constraint (unknown keys, type errors, inverted date window,
                token present in YAML, etc.).
        """
        raw: dict = yaml.load(Path(path).read_text(), Loader=_ConfigLoader) or {}

        _raise_token_error(raw)

        if allow_partial:
            return cls._load_partial(raw)
        return cls.model_validate(raw)

    @classmethod
    def _load_partial(cls, raw: dict) -> FetchConfig:
        """Build a FetchConfig where required query fields may be absent."""
        query_raw = raw.get("query", {}) or {}
        partial_query = _PartialQueryConfig.model_validate(query_raw)

        placeholder = {
            "network": partial_query.network or "__partial__",
            "fork": partial_query.fork or "__partial__",
            "test_type": partial_query.test_type or "__partial__",
            "start_date": partial_query.start_date,
            "end_date": partial_query.end_date,
            "run_id_pattern": partial_query.run_id_pattern,
            "suites": partial_query.suites,
        }
        rest = {k: v for k, v in raw.items() if k != "query"}
        obj = cls.model_validate({"query": placeholder, **rest})
        object.__setattr__(obj, "query", partial_query)  # type: ignore[arg-type]
        return obj

    def with_cli_overrides(self, **kwargs: object) -> FetchConfig:
        """Return a new `FetchConfig` with CLI-supplied overrides applied.

        Only non-None kwargs are applied; omitting a kwarg leaves the existing
        value intact. Accepted kwargs: `network`, `fork`, `test_type`,
        `start_date`, `end_date`, `run_id_pattern`, `cache_dir`.

        Returns:
            A new validated `FetchConfig`; the original is not modified.

        Raises:
            pydantic.ValidationError: If the merged result is invalid.
        """
        current_query = self.query
        query_dict: dict[str, object] = {}
        for field in (
            "network",
            "fork",
            "test_type",
            "start_date",
            "end_date",
            "run_id_pattern",
            "suites",
        ):
            val = getattr(current_query, field, None)
            if val is not None:
                query_dict[field] = val

        for key in (
            "network",
            "fork",
            "test_type",
            "start_date",
            "end_date",
            "run_id_pattern",
        ):
            if key in kwargs and kwargs[key] is not None:
                query_dict[key] = kwargs[key]

        cache_dict = self.cache.model_dump()
        if "cache_dir" in kwargs and kwargs["cache_dir"] is not None:
            cache_dict["dir"] = kwargs["cache_dir"]

        merged = {
            "query": query_dict,
            "http": self.http.model_dump(),
            "output": self.output.model_dump(),
            "cache": cache_dict,
        }
        return FetchConfig.model_validate(merged)
