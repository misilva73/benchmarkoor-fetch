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

- HTTP client for Benchmarkoor's `/api/v1/index/query/{suites,runs,test_stats}`
  PostgREST endpoints and the `/api/v1/files/repricings/results/suites/<hash>/summary.json`
  artifact (trace data).
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
  run_id_pattern: '.*-full'        # optional, regex (re.fullmatch against run_id)
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
  dir: .cache/benchmarkoor-fetch     # relative to CWD by default
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

CLI-only flags: `--config`, `--out`, `--verbose`, `--quiet`. `--verbose` and
`--quiet` are mutually exclusive; verbosity behaviour is specified in
[§10.3](#103-verbosity-and-progress).

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
| `runtimes.csv` | `output.estimator_inputs: true` | `client_name, fixture_name, test_runtime_ms`. `fixture_name` is the original `test_title`; `test_runtime_ms` is the per-run duration in milliseconds (the wire field is `test_time_ns` in nanoseconds; the client divides by 1e6). |
| `opcounts.json` | `output.estimator_inputs: true` | `{fixture_name: {opcount: float, OPCODE: count, ...}}`. |
| `bench_data.parquet` | `output.merged_parquet: true` | The full merged DataFrame: `run_id, client_name, test_title, test_file, test_name, test_opcode, test_params, test_runtime_ms, ingestion_timestamp, block_limit_million, opcount`. |
| `trace.parquet` | `output.trace_parquet: true` | Per-fixture trace: `test_title, opcount, <every opcode column>`. |
| `meta.json` | always | Run metadata: resolved `suite_hash`(es) with each suite's full `name` and `indexed_at` from the `/suites` response, `query` block as resolved, fetched-at timestamp, package version, row counts. Lets downstream consumers tell two same-network/same-fork runs apart without rehitting the API. |

`trace.parquet` is derived from `opcounts.json` at write time; it is not a
second network fetch.

**Column provenance for `bench_data.parquet`:**

- `run_id`, `client_name`, `test_title`, `ingestion_timestamp` — from
  `/test_stats`. The wire columns are `run_id`, `client`, `test_name`,
  `run_start` (Unix seconds); the client renames `client → client_name`,
  `test_name → test_title` (stripping any trailing `.txt`), and
  `run_start → ingestion_timestamp` (parsed as UTC).
- `test_runtime_ms` — wire field is `test_time_ns` (nanoseconds); divided by
  1e6 at fetch time.
- `test_file`, `test_name`, `test_opcode`, `test_params` — parsed from
  `test_title` per [§7](#7-test-title-parser-correctness).
- `block_limit_million` — parsed from `test_title` via the `benchmark_<N>M`
  token in the params (e.g. `…[fork_Amsterdam-benchmark_test-opcode_ADD--benchmark_30M]`
  → `30`). N is already in millions; no division. Integer megagas. See
  [§7](#7-test-title-parser-correctness).
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
          │ (never cached)    │   │ + disk cache      │   │ + disk cache      │
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
| `parse/titles.py` | None — fresh derivation for the new `<file>.py__<name>[<params>]` title shape (see [§7](#7-test-title-parser-correctness)). |
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
`src/benchmarkoor_fetch/parse/data/opcodes_in_test_name.txt`. The precompile
set lives as a hand-maintained literal frozenset in
`src/benchmarkoor_fetch/parse/precompiles.py`, exposed via
`get_precompiles(fork: str) -> set[str]`. The fork comes from `query.fork` in
the config, so `_add_opcount_col` always uses the right table. Today
`get_precompiles` returns the same set for every fork (the most recent one);
fork-specific gating is reserved for the future. Sourcing the set
programmatically from `ethereum/execution-specs` was considered and rejected
as too heavy a dep for ingestion — when a new precompile lands, add it to the
literal and add a regression row to scenario #23a in the unit testing plan.

---

## 7. Test-title parser correctness

The parser is the part most likely to silently regress, so it gets dedicated
attention.

- **Title shape.** `test_title` strings follow the Benchmarkoor format
  `<file>.py__<test_name>[<test_params>]`, where `<file>.py` is the bare
  fixture filename (no `tests/` directory prefix), `__` separates file from
  test name, and `<test_params>` is a dash-separated parameterisation
  list (e.g. `fork_Amsterdam-benchmark_test-opcode_MOD-mod_bits_127-benchmark_140M`).
  Titles that don't match this shape join the `unparsed_fixtures` list below.
- **Columns produced.** `parse/titles.py` adds `test_file`, `test_name`,
  `test_params`, `test_opcode`, and `block_limit_million`. `block_limit_million`
  is extracted from the `benchmark_<N>M` token in the params (N is already in
  millions — no division). Stored as a nullable integer; titles without a
  recognisable suffix leave it null.
- **Opcode derivation.** Three layers, applied in order:
  1. **Name-based map** (`_NAME_TO_OPCODE`) — most tests have a fixed opcode
     determined by `test_name` alone (e.g. `test_keccak_diff_mem_msg_sizes` →
     `KECCAK256`, `test_ether_transfers_onchain_receivers` → `ETH_TRANSFER`).
  2. **Param-based dispatch** (`_opcode_from_params`) — for tests where the
     opcode depends on a param token:
     - `test_bls12_381` / `test_bls12_381_uncachable` — pick the `bls12_*`
       token and map via `_BLS12_PARAM_TO_OPCODE`
       (`bls12_fp_to_g1` → `BLS12_MAP_FP_TO_G1`, `bls12_g1add` → `BLS12_G1ADD`, etc.).
     - `test_alt_bn128` — `bn128_add*` and `bn128_double` → `ECADD`;
       `bn128_mul*` → `ECMUL`.
     - `test_alt_bn128_uncachable` — `ec_add` → `ECADD`; `ec_mul*` → `ECMUL`.
     - `test_storage_access_cold_benchmark` / `_warm_benchmark` — first
       non-fork/benchmark token's head word is the opcode (`SLOAD`,
       `SSTORE_new` → `SSTORE`, `SSTORE new value` → `SSTORE`).
  3. **Generic `opcode_<NAME>` token** — for tests whose params embed an
     explicit `opcode_<X>` token (e.g. `test_arithmetic`, `test_log_benchmark`,
     `test_swap`, `test_push`, `test_dup`, `test_account_access`).
- **Snapshot tests.** `tests/data/sample_test_titles.txt` holds the raw
  `test_title` strings drawn from a real Benchmarkoor suite, covering each
  derivation branch above. `tests/data/sample_test_titles_expected.csv`
  holds the parsed result. `tests/test_titles_parser.py` reads the former,
  parses, and asserts equality with the latter. Regenerate via
  `python -W ignore -m benchmarkoor_fetch.parse.titles tests/data/sample_test_titles.txt > tests/data/sample_test_titles_expected.csv`
  and commit both files together.
- **Unknown title patterns.** The parser silently emits empty strings for
  titles that don't match the shape, collects them, and the pipeline emits a
  single end-of-run warning: `WARN: N unparsed fixtures: <up to 10 names>`.
  The full list lands in `meta.json` under `unparsed_fixtures` so downstream
  consumers can detect drift without scraping stderr. The run does not fail —
  unparsed rows flow through with empty parsed columns.

---

## 8. HTTP, retry, threading

Behaviour matches the reference exactly. All three index endpoints
(`/suites`, `/runs`, `/test_stats`) are PostgREST under `/api/v1/index/query`;
filters take the `<op>.<value>` form (`eq.`, `gt.`, …) and pagination is
offset-based.

- `requests.Session` with a `urllib3.Retry(total, backoff_factor, status_forcelist)`
  mounted on `https://`. Defaults from `http:` in config.
- `Authorization: Bearer <token>` header; `Prefer: count=exact` on the
  count-discovery probe (the first `/test_stats` request per run_id, sent with
  `limit=0` — the response body's `total` is read and used to compute
  `ceil(total / page_size)` pages).
- `ThreadPoolExecutor(max_workers)` for parallel page fetches **within a single
  run_id**. Across run_ids the loop stays sequential (keeps memory bounded for
  large suites and matches the reference's `tqdm` per-run progress bar).
- `tqdm` for the per-run progress bar; visible at the default `info`
  verbosity, drawn via the `_reporter.Reporter` abstraction, so library
  users can opt out by passing `reporter=Reporter(level="quiet")`. See
  [§10.3](#103-verbosity-and-progress).
- **`/suites` request shape**: server-side filter is just
  `discovery_path=eq.repricings/results` (+ `limit=page_size`); the server
  returns every repricings suite. The client parses each record's `name` with
  the regex `^(.+)-(\d{2,})-([^-]+)-([^-]+)$` (network-digits-fork-test_type)
  and filters down to `(network, fork, test_type)` client-side.
- **`/runs` request shape**: `select=run_id,timestamp`, `suite_hash=eq.<hash>`,
  `status=eq.completed`, offset-based pagination via `limit=page_size&offset=N`.
  When `start_date` is set it is converted to a Unix timestamp and sent
  server-side as `timestamp=gt.<unix_ts>`. `end_date` and `run_id_pattern` are
  applied in-process (the server doesn't expose either directly): `end_date`
  compares against the ISO date portion of the run's timestamp, and
  `run_id_pattern` is a regex matched against each `run_id` via `re.fullmatch`
  — the whole `run_id` must match. The pattern is compiled in the
  `QueryConfig` pydantic validator, so a malformed regex fails at config load
  (exit code 1), before any HTTP traffic.
- **`/test_stats` request shape**: `select=run_id,test_name,client,test_time_ns,run_start`,
  `test_time_ns=gt.0`, `run_id=eq.<run_id>`, offset-based pagination. The
  `count=exact` probe (first request, `limit=0`) returns the row count for the
  run; subsequent page requests fan out under the thread pool.
- **Trace endpoint**: separate host path, not PostgREST. GET
  `/api/v1/files/repricings/results/suites/<suite_hash>/summary.json?redirect=true`
  once per suite; the response is a `{tests: [{name, opcode_count}, ...]}`
  blob that the client transforms into `{test_title: {op: count}}` (stripping
  any trailing `.txt` on `name`).

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

- Disk cache at `cache.dir` (default `./.cache/benchmarkoor-fetch/`,
  relative to the current working directory).
- Key for **test-stats**: `{suite_hash}/test_stats/{run_id}.parquet`. `run_id`
  is immutable once recorded by Benchmarkoor, so this is the strongest cache
  key — it never goes stale.
- Key for the **trace endpoint**: `{suite_hash}/summary.json`. One artifact
  per suite.

### 9.2 Suite discovery and `list_runs` are not cached

`resolve_suites` (the `(network, fork, test_type) → suite_hash` discovery
call) and `list_runs` (the per-suite runs listing) are **always** fetched
fresh — neither is content-addressed. Discovery's "latest matching" answer
changes as new suites get indexed; the runs listing accumulates new completed
entries over time under the same `(suite_hash, start_date)` key, so a cached
response would silently miss recent runs. Both responses are tiny so refetch
is cheap. When the user provides `query.suites:` explicitly, discovery is
skipped entirely and the hashes go straight into the cache lookups above.

### 9.3 Bypass

- `--no-cache` and `cache.enabled: false` both bypass reads **and** writes for
  the whole run. Use this when a suite is still being indexed, and you want
  to force a refetch.
- Cache events are emitted via the reporter (see
  [§10.3](#103-verbosity-and-progress)): both `miss: <key>` and `hit: <key>`
  are visible only at the `verbose` level. At the default `info` level the
  cache is silent.

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

Verbosity is controlled by `--verbose` (per-event detail) or `--quiet`
(warnings/errors only); they're mutually exclusive and default to neither
(milestones + progress bar). Full specification in
[§10.3](#103-verbosity-and-progress).

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
run_ids = client.list_runs(suite_hash, start_date="2026-05-18", end_date="2026-05-20", run_id_pattern=r".*-gas")
raw_df = client.fetch_test_stats(run_ids)            # untouched columns
trace_df = client.fetch_trace(suite_hash)
bench_df, trace_df = client.parse(raw_df, trace_df)
```

`BenchmarkoorClient` mirrors the helper functions in `src/data.py` 1-to-1, so
porting existing notebooks is a matter of search-and-replace, not rewrite.

### 10.3 Verbosity and progress

All user-facing output flows through a single `Reporter` abstraction
(`src/benchmarkoor_fetch/_reporter.py`). Three levels:

| Level | What it shows | How to select on the CLI |
| --- | --- | --- |
| `quiet` | Warnings and errors only (e.g. the unparsed-fixture warning, exit-code messages). No progress bar. | `--quiet` |
| `info` *(default)* | High-level milestones — suite resolution, runs query per suite, trace-summary fetch — **plus** a `tqdm` progress bar over the per-run `test_stats` fetch loop. | *(no flag)* |
| `verbose` | Everything `info` shows, plus per-event detail: cache `hit:`/`miss:` lines and a per-run `fetching test_stats` line for each `run_id`. | `--verbose` |

Library callers get the same default (`info`) as the CLI. To mute, pass
`reporter=Reporter(level="quiet")` when constructing `BenchmarkoorClient`.
The `verbose=True` boolean kwarg is kept as a back-compat shim that
constructs `Reporter(level="verbose")` when no explicit `reporter` is given.

The reporter writes to `sys.stderr` and resolves the stream lazily on each
call, so pytest's `capsys` (which swaps `sys.stderr` per test) can capture
output written from inside a long-lived client.

Concrete milestone lines (locked by the E2E tests, so don't drift them
without updating those tests):

- `resolving suite for network=<n> fork=<f> test_type=<t>`
- `listing runs for suite <hash> (start=<d>, end=<d>)`
- `→ <N> runs in window` (indented two spaces)
- `fetching trace summary for suite <hash>`
- Progress bar desc: `fetching test_stats (suite <hash[:10]>)` — the
  suite_hash is baked into the bar so that when multiple suites are
  configured (explicit `query.suites:` list), each bar is self-identifying.
  Falls back to plain `fetching test_stats` when the fetcher is called
  without a `suite_hash` (uncached library use).

Detail lines under `--verbose`:

- `run <run_id>: fetching test_stats`
- `hit: <cache_key>` / `miss: <cache_key>`

The unparsed-fixture warning (`WARN: N unparsed fixtures`) is **not**
routed through the reporter — it bypasses every level and lands on stderr
unconditionally, including under `--quiet`. Same for HTTP/auth/config error
messages.

---

## 11. Package layout

```
benchmarkoor-fetch/
├── pyproject.toml
├── README.md
├── src/benchmarkoor_fetch/
│   ├── __init__.py                # public re-exports
│   ├── _reporter.py               # Reporter (info/detail/progress); internal
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
│   │   ├── titles.py              # test_title parser (see §7)
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
    ├── test_reporter.py           # Reporter levels + tqdm progress wrapper
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
