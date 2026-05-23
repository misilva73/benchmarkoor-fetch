# `benchmarkoor-fetch` — Implementation Plan

## 1. Goal

Build a standalone Python package that queries the [Benchmarkoor API](https://benchmarkoor-api.core.ethpandaops.io)
for EVM benchmark suites and produces clean, ready-to-analyse tabular outputs.

It is the execution performance **data-ingestion** half that feeds [`evm-gasfit`](https://github.com/misilva73/evm-gasfit/tree/main) : `benchmarkoor-fetch` produces the inputs that
`evm-gasfit` consumes.

```
┌────────────────────┐    runtimes.csv     ┌──────────────────────┐
│ benchmarkoor-fetch │ ───────────────────▶│    evm-gasfit        │
│  (this plan)       │    opcounts.json    │                      │
└────────────────────┘                     └──────────────────────┘
```

It must be usable in two modes:

- **CLI** — `benchmarkoor-fetch run --config fetch.yaml --out ./data`
- **Library** — `from benchmarkoor_fetch import BenchmarkoorClient`, returns
  DataFrames in memory for notebook exploration.

The source of truth for current behaviour is
[src/data.py](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py)
(`process_bench_data` and its helpers). Everything in that file ports over;
nothing outside it does.

---

## 2. Scope

### 2.1 In scope

- HTTP client for Benchmarkoor's `/suites`, `/runs`, `/test_stats` endpoints and
  the `/files/.../summary.json` artifact (trace data).
- Resolution of `(network, fork, test_type) → suite_hash`, with optional explicit
  suite list to bypass discovery.
- Pagination, retry, threaded fetching (preserve current behaviour).
- Test-title parsing: extract `test_file`, `test_name`, `test_opcode`, and `test_params`.
- Opcode-count merging (`opcount` column based on `test_opcode` and the `PRECOMPILES` set).
- Three output artifacts (see §5).
- Disk cache, content-addressed by `suite_hash`.

### 2.2 Out of scope

- Further per-fixture parameter parsing: the structured columns currently
  produced by `process_compute_params` and `process_stateful_params`
  (`cache_strategy`, `account_mode`, `token_name`, `existing_slots`,
  `update_*`, `value_sent_*`, etc.). Downstream consumers reparse `test_params`
  themselves if they need them.
- Any modelling, regression, glue logic, or proposal generation — that lives in
  `evm-gasfit`.
- Gas-cost lookup helpers (`get_current_gas_cost`, `get_fusaka_dict`). Those
  belong with the analysis side; this tool only ships raw measurements and
  parsed metadata.
- Multi-suite *discovery* beyond the "latest matching" logic currently in
  [src/data.py:_get_latest_benchmarkoor_suite_hash](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L75).
  If the user wants something more elaborate they pass an explicit `suites:` list.
- Mutation of any remote state. Read-only client.

---

## 3. Inputs

### 3.1 YAML config

```yaml
# Benchmarkoor query
query:
  fork: amsterdam                  # required; lowercased on load (Amsterdam == amsterdam)
  network: jochemnet               # required UNLESS `suites` is set
  test_type: compute               # required UNLESS `suites` is set (e.g. compute | stateful | …)
  start_date: "2026-05-18"         # optional, ISO date or full timestamp
  end_date: "2026-05-20"           # optional, ISO date or full timestamp; pairs with start_date for a [start, end] window
  run_type: full                   # optional, suffix on run_id
  suites:                          # optional; if set, skips discovery and (network, test_type) are no longer required
    - <suite_hash_1>
    - <suite_hash_2>

# HTTP behaviour — all optional, sensible defaults
http:
  page_size: 10000                 # default 10000
  max_workers: 5                   # threads per run_id fetch; default 5
  retries: 3                       # default 3
  backoff_factor: 2                # default 2
  retry_status: [502, 503, 524]    # default

# Output controls
output:
  estimator_inputs: true           # write runtimes.csv + opcounts.json (default true)
  merged_parquet: true             # write bench_data.parquet (default true)
  trace_parquet: true              # write trace.parquet (default true)

# Caching — defaults shown
cache:
  enabled: true
  dir: ~/.cache/benchmarkoor-fetch
```

Auth: bearer token is **never** in the config. It comes from the
`BENCHMARKOOR_TOKEN` env var (or `--token` for one-off CLI use). The library API
accepts it as a constructor argument.

`query.fork` is normalised to lowercase at load time, on whichever surface
sets it (YAML, CLI, or kwarg). Everything downstream — cache keys, HTTP
query params, `meta.json`, the precompile lookup — sees the lowercased form.
This avoids cache splits between `Amsterdam` and `amsterdam` and matches the
canonical form used by `ethereum/execution-specs`.

### 3.2 CLI overrides

Anything in `query.*` and `output.*` may be overridden by a CLI flag of the
same name. Example:

```
benchmarkoor-fetch run --config fetch.yaml \
    --fork osaka \
    --start-date 2026-05-01 \
    --end-date 2026-05-08 \
    --out ./data
```

CLI-only flags: `--config`, `--out`, `--verbose`.

---

## 4. Outputs

Written to `--out`. If `--out` is omitted, the default is
`./{earliest_run_ts}_{latest_run_ts}/` — the **actual** ISO timestamps of the
earliest and latest run included in the fetched data (read from the `runs`
endpoint's `start_ts` / `end_ts` fields after filtering). Format
`YYYY-MM-DDTHH-MM-SSZ`, e.g. `./2026-05-18T03-14-22Z_2026-05-20T17-22-09Z/`.
This is independent of whether the user set `start_date` / `end_date` in the
config — the folder always reflects what was actually fetched, not what was
requested. The directory is created if it doesn't exist; existing files inside
are overwritten. The same pair of timestamps is also recorded in `meta.json`
under `data_window`.

| File | When | Schema |
| --- | --- | --- |
| `runtimes.csv` | `output.estimator_inputs: true` | `client_name, fixture_name, test_runtime_ms`. `fixture_name` is the original `test_title`; `test_runtime_ms` is the per-run duration as returned by the API (`run_duration_ms` on the wire). |
| `opcounts.json` | `output.estimator_inputs: true` | `{fixture_name: {opcount: float, OPCODE: count, ...}}`. |
| `bench_data.parquet` | `output.merged_parquet: true` | The full merged DataFrame: `run_id, client_name, test_title, test_file, test_name, test_opcode, test_params, test_runtime_ms, ingestion_timestamp, block_limit_million, opcount`. |
| `trace.parquet` | `output.trace_parquet: true` | Per-fixture trace: `test_title, opcount, <every opcode column>`. |
| `meta.json` | always | Run metadata: resolved `suite_hash`(es) with each suite's full `name` and `indexed_at` from the `/suites` response, `query` block as resolved, fetched-at timestamp, package version, row counts. Lets downstream consumers tell two same-network/same-fork runs apart without rehitting the API. |

`trace.parquet` is derived from `opcounts.json` at write time; it is not a
second network fetch.

**Column provenance for `bench_data.parquet`:**

- `run_id`, `client_name`, `test_title`, `ingestion_timestamp` — wire passthrough
  from `/test_stats`. `test_title` is the raw fixture identifier; the others
  are returned verbatim by the API.
- `test_runtime_ms` — wire passthrough, renamed from the API's
  `run_duration_ms` field at parse time.
- `test_file`, `test_name`, `test_opcode`, `test_params` — parsed from
  `test_title` per [§7](#7-test-title-parser-correctness).
- `block_limit_million` — parsed from `test_title` per the EELS naming
  convention (the gas-limit suffix on the fixture identifier, e.g.
  `…[fork_Prague-bench_30000000_gas]` → `30`). Integer megagas. See [§7](#7-test-title-parser-correctness).
- `opcount` — joined in from the trace data; see [§6](#6-module-by-module-port-from-srcdatapy)
  (`parse/opcount.py`).

---

## 5. Pipeline architecture

```text
                              ┌───────────────────────────────┐
                              │ load_config (yaml + CLI)      │
                              │  → FetchConfig                │
                              └─────────────┬─────────────────┘
                                            │
                              ┌─────────────▼─────────────┐
                              │ resolve_suites            │
                              │  config.suites OR         │
                              │  discover_latest()        │
                              │  → List[suite_hash]       │
                              └─────────────┬─────────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    │                       │                       │
          ┌─────────▼─────────┐   ┌─────────▼─────────┐   ┌─────────▼─────────┐
          │ fetch_runs        │   │ fetch_test_stats  │   │ fetch_trace       │
          │ (per suite)       │   │ (per run_id,      │   │ (summary.json     │
          │                   │   │  threaded)        │   │  per suite)       │
          │ + disk cache      │   │ + disk cache      │   │ + disk cache      │
          └─────────┬─────────┘   └─────────┬─────────┘   └─────────┬─────────┘
                    │                       │                       │
                    └───────────────────────┼───────────────────────┘
                                            │
                              ┌─────────────▼─────────────┐
                              │ parse_titles              │
                              │  test_file / test_name /  │
                              │  test_opcode / test_params│
                              └─────────────┬─────────────┘
                                            │
                              ┌─────────────▼─────────────┐
                              │ add_opcount               │
                              │  PRECOMPILES → STATICCALL │
                              │  else → opcode count      │
                              └─────────────┬─────────────┘
                                            │
                              ┌─────────────▼─────────────┐
                              │ write_outputs             │
                              │  runtimes.csv             │
                              │  opcounts.json            │
                              │  bench_data.parquet       │
                              │  trace.parquet            │
                              │  meta.json                │
                              └───────────────────────────┘
```

---

## 6. Module-by-module port from `src/data.py`

| New module | Source in this repo |
| --- | --- |
| `client/session.py` | [src/data.py:_get_benchmarkoor_session](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L57) |
| `client/suites.py` | [src/data.py:_get_latest_benchmarkoor_suite_hash](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L75) |
| `client/runs.py` | [src/data.py:_get_all_runs_ids_from_benchmarkoor_suite_hash](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L114) |
| `client/test_stats.py` | [src/data.py:_query_test_runs_from_benchmarkoor](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L161), [src/data.py:_get_benchmarkoor_total_pages](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L148) |
| `client/traces.py` | [src/data.py:_query_traces_from_benchmarkoor](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L210) |
| `parse/titles.py` | [src/data.py:process_test_title_col](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L290), [src/data.py:extract_param_values](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L28) |
| `parse/opcount.py` | [src/data.py:_add_opcount_col](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L228) |
| `pipeline.py` | [src/data.py:process_bench_data](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L241) (outer orchestration only) |

What does **not** port:

- `process_compute_params` and `process_stateful_params` — the structured
  per-fixture columns they emit (`cache_strategy`, `account_mode`, `token_name`,
  `existing_slots`, `update_*`, `value_sent_*`, etc.) are an analysis concern
  and live with the consumer of `bench_data.parquet`, not with the ingestion
  tool.
- `get_current_gas_cost` and the `get_fusaka_dict` import — gas-cost mapping is
  an analysis concern, not a data-ingestion concern.
- The `from operation_types import CALL, STATEFUL` import — currently unused in
  `data.py` and not needed by the new tool. Only `PRECOMPILES` is needed (for
  `_add_opcount_col`).
- The `sys.path.append` at the top of `data.py` — replaced by proper package
  imports.

`opcodes_in_test_name.txt` ships as a package resource at
`src/benchmarkoor_fetch/parse/data/opcodes_in_test_name.txt`. `PRECOMPILES` is
pulled from `ethereum/execution-specs` (pinned as a dependency) and exposed as
a fork-aware mapping at `src/benchmarkoor_fetch/parse/precompiles.py` —
`get_precompiles(fork: str) -> set[str]` returns the precompile set for the
configured fork. The fork comes from `query.fork` in the config, so
`_add_opcount_col` always uses the right table.

---

## 7. Test-title parser correctness

The parser is the part most likely to silently regress, so it gets dedicated
attention.

- **Reuse, don't rewrite.** `parse/titles.py` ports
  [src/data.py:process_test_title_col](https://github.com/misilva73/evm-gas-repricings/blob/main/src/data.py#L290)
  verbatim — same regex / `np.where` calls, same column outputs (`test_file`,
  `test_name`, `test_opcode`, `test_params`). One addition on top of the
  port: a `block_limit_million` column extracted from the EELS gas-limit
  suffix in the fixture identifier (e.g. `…[fork_Prague-bench_30000000_gas]`
  → `30`). Stored as a nullable integer; titles without a recognisable
  suffix leave it null and join the `unparsed_fixtures` list below.
- **Snapshot tests.** `tests/data/sample_test_titles.txt` holds ~200 raw
  `test_title` strings drawn from a real Benchmarkoor suite. `tests/data/sample_test_titles_expected.csv`
  holds the parsed result. `tests/test_titles_parser.py` reads the former,
  parses, and asserts equality with the latter.
- **Unknown title patterns.** Today the parser silently emits `None`/`nan` for
  titles that don't match any known shape. The new tool collects these as it
  parses and emits a single warning at the end of the run:
  `WARN: N unparsed fixtures: foo, bar, baz` (truncated at 10, total count
  shown). The warning also lands in `meta.json` under `unparsed_fixtures` so
  downstream consumers can detect drift without scraping stderr. The run does
  not fail — unparsed rows still flow through with empty parsed columns.
- **Edge cases the snapshot must cover** (lifted from existing behaviour):
  - precompiles renamed: `KECCAK → KECCAK256`, `JUMPDESTS → JUMPDEST`,
    `RIPEMD160 → RIPEMD-160`, `SHA256 → SHA2-256`, `POINT → POINT_EVALUATION`,
    `BLS12_FP_TO_G1 → BLS12_MAP_FP_TO_G1`, `BLS12_FP_TO_G2 → BLS12_MAP_FP2_TO_G2`.
  - `test_alt_bn128_uncachable[add-...]` → `test_opcode = ECADD`; same for `mul`/`ECMUL`.
  - `test_ec_pairing` → `ECPAIRING`.
  - `test_bls12_381_uncachable` → opcode from upper-cased params; `test_params` cleared.
  - `test_bls12_pairing_uncachable` → `BLS12_PAIRING_CHECK`.
  - `test_storage_access` — `test_opcode` taken from `test_params`' first token.
  - `SSTORE_*` collapsed to `SSTORE`.

---

## 8. HTTP, retry, threading

Behaviour matches today exactly:

- `requests.Session` with a `urllib3.Retry(total, backoff_factor, status_forcelist)`
  mounted on `https://`. Defaults from `http:` in config.
- `Authorization: Bearer <token>` header; optional `Prefer: count=exact` when
  asking for `total` to compute pagination.
- `ThreadPoolExecutor(max_workers)` for parallel page fetches **within a single
  run_id**. Across run_ids the loop stays sequential (matches the current
  `tqdm` per-run progress bar — and keeps memory bounded for large suites).
- `tqdm` for the per-run progress bar; gated behind `--verbose` / `verbose=True`
  so library users in notebooks don't get duplicate progress bars in JupyterLab.

No async. The package stays `requests`-based; if the user wants asyncio they
can wrap `BenchmarkoorClient` in their own executor.

---

## 9. Caching

The cache is **content-addressed**: every cache key is built from inputs that
fully determine the response, so a key collision means the data is identical.
The tool decides hit vs. miss by a simple file-existence check at that key —
no `If-Modified-Since`, no etag round-trip, no manifest. If the file is there
it's loaded and no HTTP request is made; entries never expire because every
cached endpoint is keyed on an immutable `suite_hash` / `run_id`.

### 9.1 Layout

- Disk cache at `cache.dir` (default `~/.cache/benchmarkoor-fetch/`).
- Key for **runs list**: `{suite_hash}/runs/{start_ts}_{end_ts}_{run_type}.json`
  (either ts may be `none`). Encodes the user's filter — different windows
  produce different keys and don't share storage. Read once at the top of the
  pipeline; the returned `run_ids` plus the actual `start_ts`/`end_ts` of each
  run drive everything downstream (including the §4 default output folder, so
  a fully-cached run never touches the network).
- Key for **test-stats**: `{suite_hash}/test_stats/{run_id}.parquet`. `run_id`
  is immutable once recorded by Benchmarkoor, so this is the strongest cache
  key — it never goes stale.
- Key for the **trace endpoint**: `{suite_hash}/summary.json`. One artifact
  per suite.

### 9.2 Suite discovery is not cached

`resolve_suites` (the `(network, fork, test_type) → suite_hash` discovery
call) is **always** fetched fresh — its answer changes over time as new suites
get indexed, and caching "latest matching" would silently pin the tool to a
stale suite. The discovery response is tiny so this is cheap. When the user
provides `query.suites:` explicitly, discovery is skipped entirely and the
hashes go straight into the cache lookups above.

### 9.3 Bypass

- `--no-cache` and `cache.enabled: false` both bypass reads **and** writes for
  the whole run. Use this when a suite is still being indexed, and you want
  to force a refetch.
- Cache misses emit a single `print(f"miss: {key}")` under `--verbose`. Hits
  are silent.

The cache stores the **raw API responses** (not the parsed DataFrame). That
way, a change to the parser doesn't require a network refetch.

---

## 10. CLI and Python API

### 10.1 CLI

```
benchmarkoor-fetch run \
    --config fetch.yaml \
    --out ./data

benchmarkoor-fetch suites \
    --network kurtosis_devnet --fork amsterdam --test-type benchmark
    # prints resolved suite_hash + indexed_at; doesn't fetch test data
```

Exit codes: 0 success, 1 config / input error, 2 HTTP error (auth, 5xx after
retries), 3 empty result.

### 10.2 Python API

```python
from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig

# Style A — config-driven, full pipeline
config = FetchConfig.from_yaml("fetch.yaml")
client = BenchmarkoorClient(token=os.environ["BENCHMARKOOR_TOKEN"])
result = client.run(config)             # returns FetchResult
result.bench_df                          # merged DataFrame
result.trace_df                          # opcode-count DataFrame
result.write("./data")                   # writes the §4 artifacts

# Style B — granular, for notebooks that want to inspect mid-pipeline
client = BenchmarkoorClient(token=...)
suite_hash = client.resolve_suite(network="kurtosis_devnet",
                                   fork="amsterdam",
                                   test_type="benchmark")
run_ids = client.list_runs(suite_hash, start_date="2026-05-18", end_date="2026-05-20", run_type="gas")
raw_df = client.fetch_test_stats(run_ids)            # untouched columns
trace_df = client.fetch_trace(suite_hash)
bench_df, trace_df = client.parse(raw_df, trace_df)
```

`BenchmarkoorClient` mirrors the helper functions in `src/data.py` 1-to-1, so
porting existing notebooks is a matter of search-and-replace, not rewrite.

---

## 11. Package layout

```
benchmarkoor-fetch/
├── pyproject.toml
├── README.md
├── src/benchmarkoor_fetch/
│   ├── __init__.py                # public re-exports
│   ├── config.py                  # Pydantic FetchConfig, from_yaml, with_cli_overrides
│   ├── client/
│   │   ├── __init__.py            # BenchmarkoorClient — high-level facade
│   │   ├── session.py             # requests.Session + retry wiring
│   │   ├── suites.py              # resolve_suite + list_suites
│   │   ├── runs.py                # list_runs
│   │   ├── test_stats.py          # fetch_test_stats (paginated + threaded)
│   │   ├── traces.py              # fetch_trace (summary.json)
│   │   └── cache.py               # on-disk cache (read/write JSON & parquet)
│   ├── parse/
│   │   ├── titles.py              # process_test_title_col port
│   │   ├── opcount.py             # _add_opcount_col port
│   │   ├── precompiles.py         # PRECOMPILES literal
│   │   └── data/
│   │       └── opcodes_in_test_name.txt
│   ├── pipeline.py                # high-level run() — orchestrates client + parse + write
│   ├── result.py                  # FetchResult dataclass (DataFrames + write())
│   └── cli.py                     # argparse / click entry point
└── tests/
    ├── data/
    │   ├── sample_test_titles.txt
    │   ├── sample_test_titles_expected.csv
    │   ├── fake_suites_response.json
    │   ├── fake_runs_response.json
    │   ├── fake_test_stats_response.json
    │   └── fake_summary.json
    ├── test_config.py
    ├── test_titles_parser.py      # snapshot test for parse/*
    ├── test_opcount.py
    ├── test_client_http.py        # uses responses/respx to mock requests
    ├── test_cache.py
    └── test_cli.py
```

End-to-end coverage lives separately under `tests/e2e/` per
[e2e_testing_plan.md §4](./e2e_testing_plan.md#4-test-file-layout); it
supersedes the older `test_pipeline.py` idea. The unit suite above is
specified in [unit_testing_plan.md](./unit_testing_plan.md).

Public surface from `benchmarkoor_fetch/__init__.py`:

```python
from .client import BenchmarkoorClient
from .config import FetchConfig
from .result import FetchResult
from .parse.titles import parse_test_titles    # standalone parser for power users
```

---

## 12. Dependencies

Runtime (`pyproject.toml` `[project.dependencies]`):

- `requests` — HTTP
- `pandas` — DataFrames
- `numpy` — vectorised parsing in titles.py
- `pydantic` — config validation
- `pyyaml` — config file
- `tqdm` — progress bars
- `pyarrow` — parquet writer

Dev (`[dependency-groups.dev]`):

- `pytest`, `pytest-cov`
- `responses` (or `pytest-httpserver`) — for HTTP mocking in `test_client_http.py`
- `ruff` — format + lint (single tool; `ruff format` replaces black)

Python: `>=3.11` (current repo runs 3.11 under conda; matches new_project.md
implicitly).

---

## 13. Implementation order

Tests-first. E2E exercises the seams; unit tests cover the gaps E2E can't
see; the §11 modules are implemented last, against both test layers as an
executable spec.

1. **Package skeleton.** `pyproject.toml`, `src/benchmarkoor_fetch/` per §11
   with empty module files and stubbed public re-exports in `__init__.py`.
   CLI entry point registered in `[project.scripts]`; `main()` raises
   `NotImplementedError`. Verify `pip install -e ".[dev]"` and `pytest -q`
   (collecting zero tests) both succeed.

2. **E2E tests.** Per [e2e_testing_plan.md](./e2e_testing_plan.md): commit
   the canonical fixture bundle (`tests/data/e2e/responses/`,
   `tests/data/e2e/fetch.yaml`, `tests/data/e2e/golden_outputs/`), wire
   `tests/e2e/conftest.py`, and author every scenario in §5 of that plan.
   All tests fail at this point — that's the point. They form the
   executable specification of the seams.

3. **Unit tests.** Per [unit_testing_plan.md](./unit_testing_plan.md):
   author the gap-filling tests (parser snapshot, pydantic validation
   matrix, HTTP retry timing, pagination math, cache key construction,
   opcount edge cases). Bring `tests/data/sample_test_titles.txt` from a
   real suite snapshot and lock its parsed output in
   `sample_test_titles_expected.csv` in the same commit. All tests still
   fail.

4. **Implementation.** Build the §11 modules in dependency order until
   both test layers go green:
   - 4a. `config.py` → `test_config.py` green.
   - 4b. `client/session.py`, `suites.py`, `runs.py`, `test_stats.py`,
     `traces.py` → `test_client_http.py` green.
   - 4c. `client/cache.py` → `test_cache.py` green; cache-related E2E
     scenarios go green.
   - 4d. `parse/titles.py`, `parse/opcount.py`, `parse/precompiles.py` →
     `test_titles_parser.py`, `test_opcount.py` green.
   - 4e. `pipeline.py`, `result.py` → happy-path E2E scenarios go green.
   - 4f. `cli.py` → CLI E2E scenarios go green and `test_cli.py`
     (argparse coverage) green.

5. **Docs.** `README.md` with a 30-second quickstart for both CLI and
   notebook use, plus a "feeding evm-gasfit" section showing the
   two-step workflow.

6. **Publish to PyPI.** `pyproject.toml` is configured for PyPI from day
   one (project name `benchmarkoor-fetch`, classifiers,
   long_description from `README.md`). Release flow: tag → GitHub
   Actions builds sdist + wheel with `python -m build` and publishes via
   `pypa/gh-action-pypi-publish` using trusted publishing (no
   long-lived API token). The CLI entry point
   (`benchmarkoor-fetch = benchmarkoor_fetch.cli:main`) is registered
   in `[project.scripts]` so `pip install benchmarkoor-fetch` makes the
   command immediately available.
