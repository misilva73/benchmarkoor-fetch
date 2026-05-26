# benchmarkoor-fetch

Fetch EVM benchmark suites from the Benchmarkoor API and produce clean
tabular outputs (`runtimes.csv`, `opcounts.json`, `bench_data.parquet`,
`trace.parquet`, `meta.json`). Data ingestion only — no modelling or gas
analysis (that lives in
[evm-gasfit](https://github.com/misilva73/evm-gasfit)).

## Source of truth

The design contract lives in [.claude/](./):

- [implementation_plan.md](implementation_plan.md) — scope, inputs,
  outputs, pipeline, HTTP/cache behaviour, CLI/API, layout, deps.
- [e2e_testing_plan.md](e2e_testing_plan.md) — E2E scenarios.
- [unit_testing_plan.md](unit_testing_plan.md) — unit scenarios.

Read these before adding code. If you want to deviate, raise it **before**
writing — either the plan is wrong (update it) or the change isn't justified.

This package ports `process_bench_data` and its helpers from
[misilva73/evm-gas-repricings/src/data.py](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py).
Everything else in that repo is analysis and stays out. The plan lists
features intentionally dropped in the port; don't re-add them without
updating the plan first.

## Conventions

- **Less is more.** Simple, short, readable code over clever. Reach for a
  well-maintained dep before writing a helper.
- **Stack.** `pandas` (DataFrames), `numpy` (vectorised parsing), `pydantic`
  v2 (config schemas, validated at load), `pyyaml` (config), `requests` +
  `urllib3.Retry` (HTTP), `pyarrow` (parquet), `tqdm` (progress), `responses`
  (HTTP mocks). Don't mock `pandas`/`numpy`/`pyarrow`/`pyyaml`.
- **Port verbatim where the plan says so.** `parse/opcount.py` is a
  line-for-line port; refactor only after the snapshot test passes.
  `parse/titles.py` is a fresh derivation, not a port.
- **Tests-first.** Don't start a module before tests pinning its behaviour
  exist. E2E lives in `tests/e2e/` and mocks only the network boundary; unit
  tests live in `tests/test_*.py`. The parser snapshot
  (`tests/data/sample_test_titles_expected.csv`) must be regenerated and
  committed in the **same** change as any `parse/titles.py` edit.
- **Public API is small.** `BenchmarkoorClient`, `FetchConfig`,
  `FetchResult`, `parse_test_titles`; CLI is `benchmarkoor-fetch run|suites`.
  Output filenames and column names are pinned by the plan — downstream
  `evm-gasfit` reads them, so renames need plan + consumer coordination.
- **Auth never in config.** Bearer token via `BENCHMARKOOR_TOKEN` env,
  `--token` CLI flag, or `BenchmarkoorClient(token=…)` kwarg only.
- **Read-only client.** No POST/PUT/DELETE — the API is treated as immutable.
- **Cache is content-addressed and never expires by default.** A hit means
  the bytes are guaranteed identical. Suite discovery (`resolve_suites`) is
  intentionally not cached.
- **Runtimes are milliseconds end-to-end.** Wire field `run_duration_ms`,
  CSV column `test_runtime_ms`. Conversion to seconds belongs in `evm-gasfit`.
- **Unparsed fixture titles are a warning, not a failure.** Rows flow through
  with empty parsed columns; the run emits one end-of-run warning and records
  them under `unparsed_fixtures` in `meta.json`.
- **CLI exit codes.** 0 success, 1 config/input error, 2 HTTP error, 3 empty
  result. Don't add new codes without updating the plan.
- **No references to Claude or `.claude/` in shipped artifacts.** Code, tests,
  docstrings, commits, README, generated `meta.json` — the plan is internal
  scaffolding, not a citation target. Inline the rule itself.
- **Python ≥ 3.11.** `ruff` for both lint and format (no black). Prefer
  `from __future__ import annotations` and PEP 604 unions (`X | None`).
  `pathlib.Path` for all filesystem paths; public API types paths as `Path`
  and converts to `str` only at library boundaries that require it.
- **Google-style docstrings on the public API only.** Auto-rendered via
  `mkdocstrings` (see [mkdocs.yml](../mkdocs.yml)) — update the docstring, not a
  parallel hand-written reference. Omit docstrings that just restate the
  signature.

## Commands

```bash
pip install -e ".[dev]"                   # dev environment (tests, lint)
pip install -e ".[dev,docs]"              # add mkdocs/mkdocstrings

pytest                                     # full test suite
pytest tests/test_titles_parser.py -v      # single file

ruff check src tests                       # lint (CI gate)
ruff format --check src tests              # format check (CI gate)
ruff format src tests                      # apply formatting

mkdocs serve                               # preview docs locally
python -m build                            # build sdist + wheel

# Regenerate the parser snapshot after an intentional parser change.
# Commit the updated CSV in the same change as the parser edit.
python -m benchmarkoor_fetch.parse.titles \
    tests/data/sample_test_titles.txt \
    > tests/data/sample_test_titles_expected.csv

# Smoke-test the CLI against the E2E fixture (mocked in tests; real HTTP here).
benchmarkoor-fetch run --config tests/data/e2e/fetch.yaml --out /tmp/out --no-cache
```

## Release

1. Bump `version` in [pyproject.toml](../pyproject.toml) and update
   [CHANGELOG.md](../CHANGELOG.md).
2. Commit, then tag `vX.Y.Z` and push the tag.
3. [release.yml](../.github/workflows/release.yml) builds and publishes to PyPI
   via trusted publishing on tag push.
4. Docs deploy from `main` via [docs.yml](../.github/workflows/docs.yml).

## Layout

Top-level dirs: [src/benchmarkoor_fetch/](../src/benchmarkoor_fetch/) (with
`client/` and `parse/` subpackages), [tests/](../tests/) (with `e2e/` and
`data/` subdirs). The plan documents the module-by-module breakdown.
