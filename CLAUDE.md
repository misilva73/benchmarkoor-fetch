# benchmarkoor-fetch

Fetch EVM benchmark suites from the Benchmarkoor API and produce clean tabular
outputs. Standalone Python package; data-ingestion only (no modelling, no gas
analysis).

## Status

Greenfield. The repo currently contains only the LICENSE and the design
documents — **no package skeleton yet**. Workflow is tests-first: scaffold
the package, then author every E2E + unit test (red), then implement modules
until both layers go green. See implementation_plan.md §13.

## Source of truth

Three design documents under [.claude/](.claude/). Read all three before
adding code.

- [.claude/implementation_plan.md](.claude/implementation_plan.md) — the
  design contract. Sections:
  - §2 scope (what ports from the reference, what intentionally doesn't)
  - §3 inputs (YAML config, CLI overrides, env-var auth)
  - §4 outputs (`runtimes.csv`, `opcounts.json`, `bench_data.parquet`,
    `trace.parquet`, `meta.json`)
  - §5 pipeline architecture
  - §6 module-by-module port from the reference `src/data.py`
  - §7 test-title parser correctness (snapshot tests + unparsed-fixture warning)
  - §8 HTTP / retry / threading behaviour
  - §9 disk cache (content-addressed, never-expire by default)
  - §10 CLI and Python API contracts
  - §11 package layout
  - §12 dependencies
  - §13 implementation order (tests-first: skeleton → E2E → unit → impl
    4a-4f → docs → publish)
- [.claude/e2e_testing_plan.md](.claude/e2e_testing_plan.md) — full E2E
  scenario list (#1–#39) against a mocked Benchmarkoor API, with the
  fixture and golden-bundle layout under `tests/e2e/` and `tests/data/e2e/`.
- [.claude/unit_testing_plan.md](.claude/unit_testing_plan.md) — gap-filling
  unit scenarios for `test_config.py`, `test_titles_parser.py`,
  `test_opcount.py`, `test_client_http.py`, `test_cache.py`, `test_cli.py`.

If you find yourself wanting to deviate from any of these, surface that
**before** writing code — either the doc is wrong (update it) or the change
isn't justified. Don't silently expand scope.

## Reference implementation

This package is a port of
[misilva73/evm-gas-repricings/src/data.py](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py)
— specifically `process_bench_data` and its helpers. The plan §6 maps each new
module to a line range in that file. Everything outside `data.py` in the
reference repo is **analysis** and belongs in
[evm-gasfit](https://github.com/misilva73/evm-gasfit), not here.

Features **intentionally dropped** in the port (see plan §2.2 and §6):

- `process_compute_params` / `process_stateful_params` and their structured
  columns (`cache_strategy`, `account_mode`, `token_name`, `existing_slots`,
  `update_*`, `value_sent_*`, …). Consumers reparse `test_params` themselves.
- `get_current_gas_cost`, `get_fusaka_dict` — analysis concern.
- The `operation_types.CALL` / `STATEFUL` imports (unused). Only `PRECOMPILES`
  is needed.
- The `sys.path.append` shim — replaced by proper package imports.

Don't re-add dropped features without updating the plan first.

## Working conventions

- **Less is more.** Prefer simple, short, readable code over clever or
  over-engineered solutions. Optimize for the next reader.
- **Don't reinvent the wheel.** If a widely used, well-maintained library
  already does X, depend on it instead of porting or reimplementing X. Reach
  for the dep before writing the helper. Examples already locked in by the
  plan: `requests` for HTTP, `urllib3.Retry` for backoff, `tqdm` for progress,
  `responses` for HTTP mocking, `pyarrow` for parquet.
- **Port verbatim where the plan says so.** `parse/titles.py` and
  `parse/opcount.py` are line-for-line ports of the reference — same regexes,
  same `np.where` calls, same column outputs. Don't "clean them up" while
  porting; refactor after the snapshot test passes, not before.
- **Stack defaults.** `pandas` for DataFrames; `numpy` for vectorised parsing;
  `pydantic` v2 for config schemas; `pyyaml` for the config file; `pyarrow`
  for parquet I/O.
- **No references to Claude or the plan in shipped artifacts.** Code, tests,
  docstrings, comments, commit messages, and user-facing docs (README,
  generated `meta.json`) must not mention Claude, "AI agents", the `.claude/`
  directory, or `.claude/implementation_plan.md`. The plan is an internal
  design document — treat it as scaffolding, not a citation target. When
  you'd otherwise write "per the plan §X", inline the rule itself or point at
  the relevant module/test instead.
- **Tests-first.** Per implementation_plan.md §13: scaffold the skeleton,
  author every E2E scenario (red), author every unit scenario (red), then
  implement modules in dependency order until both layers go green. Don't
  start a §11 module before the tests pinning its behaviour exist.
  - **E2E** lives in `tests/e2e/` against a mocked Benchmarkoor API. Scope
    and scenarios in [.claude/e2e_testing_plan.md](.claude/e2e_testing_plan.md).
    Owns exit codes, default `--out`, cache lifecycle across runs, golden
    artifact diffs, determinism.
  - **Unit** lives in `tests/test_*.py`. Scope and scenarios in
    [.claude/unit_testing_plan.md](.claude/unit_testing_plan.md). Covers
    pydantic validation, parser correctness, HTTP retry timing, pagination
    math, cache key construction, opcount edge cases.
  - **Parser snapshot** (`test_titles_parser.py`) locks
    `sample_test_titles.txt` → `sample_test_titles_expected.csv`. Any
    parser change must be reflected in the expected CSV in the same commit.
  - Mock only the network boundary (via `responses`). Don't mock
    `pandas`/`numpy`/`pyarrow`/`pyyaml`.
- **Public API is small.** From `benchmarkoor_fetch/__init__.py`:
  `BenchmarkoorClient`, `FetchConfig`, `FetchResult`, `parse_test_titles`.
  The CLI is `benchmarkoor-fetch run …` and `benchmarkoor-fetch suites …`
  (see plan §10). Everything else is internal.
- **Auth never lives in the config.** Bearer token comes from the
  `BENCHMARKOOR_TOKEN` env var, the `--token` CLI flag, or the
  `BenchmarkoorClient(token=…)` kwarg. Don't accept it from YAML.
- **Pydantic v2** for `FetchConfig`. Validate at load time; downstream code
  trusts the parsed model.
- **Read-only client.** The Benchmarkoor API is treated as immutable — no
  POST/PUT/DELETE anywhere in this package.
- **Python ≥ 3.11**, `ruff` for both lint and format (single tool — no black).
  Prefer `from __future__ import annotations` and PEP 604 unions (`X | None`).
- **`pathlib.Path` for all filesystem paths.** No `os.path`, no string
  concatenation, no raw `"/"` literals. Use `Path` for joining (`p / "x.csv"`),
  reading/writing (`.read_text()`, `.write_text()`), and metadata
  (`.exists()`, `.parent`, `.stem`). Public API functions accepting paths
  should type them as `Path` (callers convert at the boundary); convert to
  `str` only when handing off to a library that requires it (e.g. `requests`
  URL params).
- **Google-style docstrings on the public API.** `BenchmarkoorClient`,
  `FetchConfig`, `FetchResult`, `parse_test_titles`, and the CLI entry point.
  Type hints carry the types; use the docstring body for behavioural notes
  only. Omit docstrings that would just restate the signature.

## Commands

```bash
# install in editable mode with dev extras
pip install -e ".[dev]"

# run the full test suite
pytest

# run a single test file
pytest tests/test_titles_parser.py -v

# regenerate the parser snapshot after an intentional parser change
# (then commit the updated expected CSV in the same change)
python -m benchmarkoor_fetch.parse.titles \
    tests/data/sample_test_titles.txt \
    > tests/data/sample_test_titles_expected.csv

# smoke-test the CLI against the E2E fixture (mocked in tests, real HTTP if run directly)
benchmarkoor-fetch run --config tests/data/e2e/fetch.yaml --out /tmp/out --no-cache
```

## Layout (target — see plan §11)

```
src/benchmarkoor_fetch/
├── __init__.py                  # public re-exports
├── config.py                    # FetchConfig (pydantic) + from_yaml + CLI overrides
├── client/
│   ├── __init__.py              # BenchmarkoorClient facade
│   ├── session.py               # requests.Session + Retry wiring
│   ├── suites.py                # resolve_suite, list_suites
│   ├── runs.py                  # list_runs
│   ├── test_stats.py            # fetch_test_stats (paginated + threaded)
│   ├── traces.py                # fetch_trace (summary.json)
│   └── cache.py                 # content-addressed on-disk cache
├── parse/
│   ├── titles.py                # process_test_title_col port
│   ├── opcount.py               # _add_opcount_col port
│   ├── precompiles.py           # fork-aware PRECOMPILES set
│   └── data/
│       └── opcodes_in_test_name.txt
├── pipeline.py                  # orchestrates client + parse + write
├── result.py                    # FetchResult dataclass
└── cli.py                       # argparse / click entry point

tests/
├── data/                        # unit fixtures: parser snapshot, http stubs
│   └── e2e/                     # E2E fixtures: responses/, golden_outputs/
├── e2e/                         # E2E suite — see e2e_testing_plan.md §4
│   ├── conftest.py
│   ├── test_cli_run.py
│   ├── test_cli_suites.py
│   ├── test_library_api.py
│   ├── test_outputs.py
│   ├── test_cache_lifecycle.py
│   ├── test_errors.py
│   └── test_determinism.py
├── test_config.py
├── test_titles_parser.py
├── test_opcount.py
├── test_client_http.py
├── test_cache.py
└── test_cli.py
```

## Notes for AI agents

- Before editing a module, re-read the relevant plan section — the plan is
  the contract, not the existing code (which doesn't exist yet).
- The plan §4 pins exact output filenames (`runtimes.csv`, `opcounts.json`,
  `bench_data.parquet`, `trace.parquet`, `meta.json`) and exact column names.
  Downstream `evm-gasfit` reads these — don't rename or restructure outputs
  without updating the plan **and** coordinating with the consumer.
- Default output folder when `--out` is omitted is
  `./{earliest_run_ts}_{latest_run_ts}/` using the **actual** ISO timestamps
  of the runs included after filtering, formatted `YYYY-MM-DDTHH-MM-SSZ`.
  Not the configured `start_date`/`end_date`. See plan §4.
- CLI exit codes are 0 success, 1 config/input error, 2 HTTP error, 3 empty
  result. Don't add new codes without updating the plan.
- Cache is content-addressed and **never expires by default** — keys are
  built so that a hit means the bytes are guaranteed identical. Suite
  discovery (`resolve_suites`) is **not** cached on purpose; see plan §9.2.
- Runtimes are **milliseconds** on the wire (`run_duration_ms`); the
  `runtimes.csv` column is `test_runtime_ms`. Don't convert to seconds in
  this package — that's `evm-gasfit`'s job.
- The fixture title parser is the part most likely to silently regress.
  Always run `test_titles_parser.py` after any change in `parse/titles.py`,
  and if the snapshot needs to change, update
  `sample_test_titles_expected.csv` in the **same** commit.
- Unparsed fixture titles are a warning, not a failure: rows flow through
  with empty parsed columns, and the run emits a single end-of-run warning
  + records them under `unparsed_fixtures` in `meta.json` (plan §7).