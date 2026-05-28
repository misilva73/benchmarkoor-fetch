# `benchmarkoor-fetch` — Unit Testing Plan

Complements [implementation_plan.md](./implementation_plan.md) and
[e2e_testing_plan.md](./e2e_testing_plan.md). This document covers **unit
tests** — the gap-filling layer beneath E2E, scoped to individual modules.

---

## 1. Goal

E2E proves the seams. Unit tests prove the cells. They catch regressions that
E2E can't economically see:

- Parser correctness across many title shapes — one E2E sample wouldn't catch a
  regression in a rarely-exercised regex branch.
- Pydantic validation rules — error messages, field coercion, default
  population, override precedence at the model level.
- HTTP retry timing and pagination math — E2E only asserts the user-visible
  outcome (`exit 0` vs `exit 2`), not how many attempts or in what shape.
- Cache key construction — proving content-addressing actually works at the
  key level, not just observationally.
- Opcount logic across opcode shapes (regular, precompile, unknown,
  fork-aware).

Non-goal: re-testing pipeline glue. If a test only passes when the full
pipeline runs, it belongs in [e2e_testing_plan.md](./e2e_testing_plan.md).

---

## 2. Test policy

- **One module per test file.** Each [§11](./implementation_plan.md#11-package-layout)
  module gets a paired `tests/test_<module>.py`.
- **Mock only the network.** Use `responses` (already in §12 dev deps) for
  HTTP. Never mock `pandas`/`numpy`/`pyarrow`/`pyyaml` — those are part of the
  contract.
- **Fixtures live next to unit tests.** `tests/data/sample_test_titles*` for
  the parser snapshot; tiny per-endpoint stubs under `tests/data/http/` and
  `tests/data/opcount/`. The E2E fixture tree at `tests/data/e2e/` is
  off-limits to unit tests — different layer, different fixtures.
- **Snapshot for the parser.** `sample_test_titles.txt` and
  `sample_test_titles_expected.csv` lock the regex output. Snapshot
  regeneration is explicit; see §6.
- **Fast.** Each test < 100 ms; full unit suite well under 5 s. If a unit
  test is slow, that's a code smell — fix the test, not the threshold.
- **No retesting of seams.** If a test would pass even with the module under
  test stripped out (i.e. it's exercising pipeline integration), move it to
  E2E.

---

## 3. Fixture layout

```
tests/data/
├── sample_test_titles.txt          # representative titles drawn from a live suite, one per derivation branch
├── sample_test_titles_expected.csv # parsed snapshot — regenerated explicitly
├── opcount/
│   ├── regular_opcode.parquet      # trace df: ADD=42, MUL=3, …
│   ├── precompile_opcode.parquet   # trace df: STATICCALL=7
│   └── unknown_opcode.parquet
└── http/
    ├── suites_two_matching.json    # two indexed suites, different timestamps
    ├── runs_three.json             # three runs spanning a window
    ├── test_stats_page1.json       # multi-page pagination drill
    ├── test_stats_page2.json
    └── summary_minimal.json
```

E2E and unit fixtures stay disjoint on purpose: a change to one suite's
fixtures should never silently rebalance the other.

---

## 4. Test file layout

```
tests/
├── test_config.py
├── test_titles_parser.py
├── test_opcount.py
├── test_client_http.py
├── test_cache.py
├── test_reporter.py
└── test_cli.py
```

`test_pipeline.py` from the original implementation plan is absorbed into
`tests/e2e/` per [e2e_testing_plan.md §4](./e2e_testing_plan.md#4-test-file-layout).

---

## 5. Scenarios

Each row is one test. "Asserts" is the *minimum* assertion set — tests may
check more, but at least these must hold.

### 5.1 Config — `test_config.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 1 | Full valid YAML loads to `FetchConfig` | Every [§3.1](./implementation_plan.md#31-yaml-config) field parsed correctly; omitted sections get the documented defaults. |
| 2 | Missing required query field | Parametrized over `network`, `fork`, `test_type` (and any other required field added to `query` later). Each raises `pydantic.ValidationError` whose message names the missing field. |
| 5 | `start_date` alone is allowed | Loads cleanly; `end_date is None`. |
| 6 | `end_date` alone is allowed | Loads cleanly; `start_date is None`. |
| 7 | `start_date > end_date` rejected | Error mentions the inverted window. |
| 8 | Invalid ISO date string rejected | Error names the offending field and value. |
| 9 | Token cannot live in YAML | YAML containing `token:` (at any nesting) is rejected with a clear "auth must come from env / `--token` / kwarg" message. |
| 10 | `http` defaults populated | `page_size=10000`, `max_workers=5`, `retries=3`, `backoff_factor=2`, `retry_status=[502,503,524]`. |
| 11 | `output` defaults populated | All three output flags default to `True`. |
| 12 | `cache` defaults populated | `enabled=True`, `dir == Path(".cache/benchmarkoor-fetch")` (relative to CWD). |
| 13 | CLI overrides applied | `with_cli_overrides(fork="osaka", start_date="2026-05-01")` replaces the YAML values; non-overridden fields untouched. |
| 14 | Override of a missing required field still validates | Loading YAML without `fork` then calling `with_cli_overrides(fork="osaka")` succeeds. |
| 15 | Explicit `query.suites:` list parses to a sequence of strings | Hashes round-trip without coercion. |
| 16 | Unknown top-level YAML key rejected | Prevents silent typos in `chache:` etc. |
| 16a | `query.fork` lowercased on load | YAML `fork: Amsterdam` → `config.query.fork == "amsterdam"`. `with_cli_overrides(fork="OSAKA")` → `"osaka"`. Same applies when constructing a `FetchConfig` directly with a mixed-case fork kwarg. Locks the §3.1 normalisation rule. |

### 5.2 Title parser — `test_titles_parser.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 17 | Snapshot diff | `parse_test_titles(read(sample_test_titles.txt))` equals `read(sample_test_titles_expected.csv)` row-by-row, including ordering. |
| 19 | Unparsed titles flow through with empty parsed columns | A title that matches no pattern → row exists; `test_file`, `test_name`, `test_opcode`, `test_params`, `block_limit_million` all empty/NaN; no exception. |
| 20 | Parser returns unparsed titles alongside the DataFrame | `parse_test_titles(df) -> (df, unparsed: list[str])`. Warning emission is the pipeline's job, not the parser's. |
| 21 | Idempotent on already-parsed input | Calling parse twice yields identical output (catches accidental in-place mutation). |
| 21a | `block_limit_million` extracted from `benchmark_<N>M` token | Title `…[fork_Amsterdam-benchmark_test-…-benchmark_300M]` → `block_limit_million == 300` (int). N is already in millions, no division. Title with no `benchmark_<N>M` token → null. Tested via dedicated cases plus implicit coverage by the snapshot (#17). |

### 5.3 Opcount — `test_opcount.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 22 | Regular opcode lookup | Row with `test_opcode="ADD"` and trace `ADD=42` → `opcount == 42`. |
| 23 | Precompile uses STATICCALL count | Row with `test_opcode="ECADD"` (∈ PRECOMPILES) and trace `STATICCALL=7` → `opcount == 7`. |
| 23a | Address-only precompiles in PRECOMPILES | `test_opcode="ECRECOVER"` and `test_opcode="P256VERIFY"` route through `STATICCALL` (i.e. both names are members of `get_precompiles(fork)`). Regression: these have no matching trace column, so omission silently yields `opcount=0` while scenario #23 still passes. |
| 24 | Unknown opcode → 0 | Row with `test_opcode="FOO"` not in trace → `opcount` matches the port's behaviour (literal 0 or NaN — match `_add_opcount_col`). |
| 25 | Missing `test_opcode` → NaN | Row with empty `test_opcode` → `opcount` is NaN, not 0. |
| 26 | Fork-aware precompile resolution | `add_opcount(df, trace, fork="osaka")` calls `get_precompiles("osaka")` (verify via monkeypatched spy). |
| 27 | Trace-row alignment | Multiple titles sharing the same `test_title` resolve to the same `opcount` (no off-by-one merge bugs). |

### 5.4 HTTP client — `test_client_http.py`

All tests in this file use `responses` to mock the wire.

| # | Scenario | Asserts |
| --- | --- | --- |
| 31 | `build_session` wires `urllib3.Retry` from config | Mounted `https://` adapter has `max_retries.total == config.http.retries`, `backoff_factor == config.http.backoff_factor`, `status_forcelist == config.http.retry_status`. |
| 32 | `resolve_suite` request shape | GET `/api/v1/index/query/suites` with `discovery_path=eq.repricings/results` (+ `limit`); no `network`/`fork`/`test_type` on the wire — those are matched client-side against each suite's `name` via the `^(.+)-(\d{2,})-([^-]+)-([^-]+)$` regex. `Authorization: Bearer …` header present. |
| 33 | `resolve_suite` picks latest by `indexed_at` | With two matching suites in the mocked response, returns the hash with the later `indexed_at`. |
| 34 | `list_runs` request shape + client-side filtering | URL carries `select=run_id,timestamp`, `suite_hash=eq.<hash>`, `status=eq.completed`, and (when `start_date` is set) `timestamp=gt.<unix_ts>`. `end_date` and `run_id_pattern` never appear on the wire. A companion test pins the filter behaviour: given a multi-day mocked payload with varied `run_id` suffixes, `list_runs(..., start_date=…, end_date=…, run_id_pattern=…)` drops records past `end_date` and records whose `run_id` does not satisfy `re.fullmatch(pattern, run_id)`. A third companion test pins the config-load validation: `QueryConfig(run_id_pattern="[unclosed")` raises a pydantic `ValidationError` (no HTTP, no `list_runs` invocation). |
| 35 | `fetch_test_stats` pagination | One parametrized test over `(total, page_size)`. Covers: the count-header round-trip (`Prefer: count=exact` with `limit=0` on the first request, the body's `total` field read into the page count), the ceiling-division boundary (`total=20, page_size=10` → exactly 2 page requests, not 3), an uneven page (`total=25, page_size=10` → 3 page requests at `offset=0,10,20`), and the empty case (`total=0` → zero page requests; returns an empty DataFrame with the documented columns — not `None`, not an exception). |
| 36c | `fetch_test_stats` renames + converts `test_time_ns` → `test_runtime_ms` | Mocked response with `test_time_ns: 1_234_000_000` → returned DataFrame has column `test_runtime_ms` with value `1234` (ns divided by 1e6); no `test_time_ns` or `run_duration_ms` column survives. The `client`/`test_name`/`run_start` wire columns are likewise renamed to `client_name`/`test_title`/`ingestion_timestamp`. Locks the §4 wire-to-column mapping. |
| 37 | `fetch_test_stats` threading | `ThreadPoolExecutor(max_workers=config.http.max_workers)` is the executor used for pages within one `run_id`; concurrent requests verified by patching `ThreadPoolExecutor.__init__` (assert `max_workers` kwarg) and counting concurrent in-flight `responses` calls via a callback. Avoids wall-clock timing. |
| 38 | `fetch_test_stats` is sequential across `run_id`s | Two run_ids → the second `run_id`'s first page is not requested until the first `run_id` is fully fetched. |
| 39 | `fetch_trace` URL shape | GET to `/api/v1/files/repricings/results/suites/<suite_hash>/summary.json?redirect=true` exactly once per suite. |
| 40 | 502 → 502 → 200 succeeds | With `retries=3`, two 502 responses then a 200 → returns the parsed body without raising. Retry count visible via `responses.calls`. |
| 41 | 502 exhausted raises | More 502s than `retries+1` → raises `requests.HTTPError`. |
| 42 | 401 surfaces immediately, no retry | `responses.calls` records exactly one request; exception type distinguishes auth from generic 5xx. |
| 43 | Bearer token kwarg beats env | `BenchmarkoorClient(token="X")` while `BENCHMARKOOR_TOKEN=Y` is set → captured header is `Bearer X`. |
| 43a | Env fallback when no kwarg | `BenchmarkoorClient()` with `BENCHMARKOOR_TOKEN=Y` set (no `token=` kwarg) → captured header is `Bearer Y`. Mirror of #43 with the precedence reversed. |
| 43b | Missing token at library level raises | `BenchmarkoorClient()` with `BENCHMARKOOR_TOKEN` unset → raises at construction (not at first request), with a message naming the env var. Mirrors the CLI exit-1 behaviour from E2E #31. |
| 44 | Read-only client never mutates | `responses` registered with no POST/PUT/DELETE allowances; running every client method against the canonical fixtures still passes (i.e. no such method is even attempted). |
| 44a | `client.parse(raw_df, trace_df)` Style-B wrapper | Returns `(bench_df, trace_df)`. `bench_df` columns match the §4 schema; `trace_df` is the projection of opcounts. Wrapper is the same code path as `client.run(config)` produces, so result equals running the full pipeline against the same raw inputs (modulo non-deterministic columns). |

### 5.5 Cache — `test_cache.py`

All tests use `tmp_path` for the cache directory.

| # | Scenario | Asserts |
| --- | --- | --- |
| 46 | Test-stats key shape | `<suite>/test_stats/<run_id>.parquet`. |
| 47 | Summary key shape | `<suite>/summary.json`. |
| 48 | Miss writes, hit reads | First call writes a file at the resolved key; second call (same key) does not invoke the fetcher (spy on the fetcher). |
| 51 | `enabled=False` bypasses read and write | Cache dir untouched after a run; fetcher invoked every time. |
| 52 | `list_runs` is never cached on disk | Two back-to-back `list_runs` calls with the same `suite_hash`/`start_date`/`end_date`/`run_id_pattern` and a configured `cache_dir` produce two HTTP calls (spy on the fetcher) and zero `runs-from-*.json` (or any runs-related) files under the cache directory. Locks the §9.2 rule that the runs listing accumulates new entries over time and therefore isn't content-addressed. |
| 53 | Discovery is not wrapped | `resolve_suite` never writes a cache file; no `/suites` artifact ever appears on disk. |
| 54 | Cache stores raw response | The parquet file at the test-stats key can be loaded back into a DataFrame whose columns match the API JSON exactly (`run_id, test_name, client, test_time_ns, run_start`) — the rename / unit conversion to `test_runtime_ms` etc. happens after the cache write, not before. |
| 55 | `verbose=True` emits `miss: <key>` once per miss | Hits stay silent within the same call (the line is emitted by the reporter's `detail` channel). |
| 55b | `verbose=True` emits `hit: <key>` once per cache hit | Mirror of #55 for the read path. Locks the symmetric `hit:` / `miss:` surface described in [implementation_plan.md §9.3](./implementation_plan.md#93-bypass). |

### 5.6 CLI argparse — `test_cli.py`

E2E (`tests/e2e/test_cli_run.py`, `test_cli_suites.py`) owns behavioural
outcomes — exit codes, output files, default `--out`. This file owns argument
parsing only.

| # | Scenario | Asserts |
| --- | --- | --- |
| 56 | `run` parses core flags | `--config`, `--out`, `--token`, `--verbose`, `--no-cache` populate the parsed args namespace. |
| 56b | `run --quiet` parses to `args.quiet=True` | And leaves `args.verbose=False`. Locks the [§3.2](./implementation_plan.md#32-cli-overrides) flag set. |
| 56c | `--verbose` and `--quiet` are mutually exclusive | argparse exits non-zero when both are passed. (E2E #8f covers the same rule end-to-end; this test pins it at the parser layer to give a clearer failure when someone re-adds the flags as independent options.) |
| 57 | `run` parses query overrides | `--network`, `--fork`, `--test-type`, `--start-date`, `--end-date` propagate to the resulting `FetchConfig` via `with_cli_overrides`. |
| 58 | `suites` parses flags | `--network`, `--fork`, `--test-type` populate the parsed args namespace. |
| 59 | Missing `--config` on `run` | argparse exits non-zero; stderr names the missing arg. |
| 60 | Unknown subcommand | Exit 1; stderr lists `run`, `suites` as available. |

### 5.7 Reporter — `test_reporter.py`

Locks the contract of `benchmarkoor_fetch._reporter.Reporter` — the single
point where the package speaks to the user. The levels and channels are
specified in [implementation_plan.md §10.3](./implementation_plan.md#103-verbosity-and-progress).

| # | Scenario | Asserts |
| --- | --- | --- |
| R1 | `level="quiet"` silences info and detail | Both `reporter.info(msg)` and `reporter.detail(msg)` produce no stderr / stdout output. |
| R2 | `level="info"` writes info, silences detail | `reporter.info(msg)` lands on stderr; `reporter.detail(msg)` does not. |
| R3 | `level="verbose"` writes info and detail | Both channels land on stderr. |
| R4 | `progress(iterable, total, desc)` yields every item at every level | Parametrized over `quiet`/`info`/`verbose`: `list(reporter.progress(items, ...))` equals the source list. The wrapper must not drop, dedupe, or reorder items. |
| R5 | `progress` draws a `tqdm` bar at `info` and `verbose` | stderr contains the `desc` and a counter (`N/N`). Captured stdout stays empty. |
| R6 | `progress` is silent at `quiet` | No stderr output produced when iterating through it. |
| R7 | `info` and `detail` never write to stdout | Even at `verbose`, both channels go exclusively to stderr — keeps machine-readable stdout (e.g. `suites` subcommand output) clean. |
| R8 | Invalid level rejected | `Reporter(level="loud")` raises `ValueError`. |
| R9 | Default level is `info` | `Reporter()` with no kwargs writes info but suppresses detail. |
| R10 | Reporter resolves `sys.stderr` lazily | Capturing stderr (e.g. `pytest`'s `capsys` swapping `sys.stderr` per test) sees reporter output even when the reporter was constructed before the swap. Locks the lazy-stream-resolution rule from §10.3. |

---

## 6. Regenerating the parser snapshot

When `parse/titles.py` legitimately changes (new regex branch, a newly
renamed opcode, a corrected bug in a precompile rename, etc.) the snapshot
moves with it. The flow is explicit, never implicit:

1. Run the parser against the raw sample file and overwrite the expected
   CSV (`-W ignore` suppresses the `runpy` warning that would otherwise
   leak into the CSV header):
   ```
   python -W ignore -m benchmarkoor_fetch.parse.titles \
       tests/data/sample_test_titles.txt \
       > tests/data/sample_test_titles_expected.csv
   ```
2. Review the diff manually.
3. Commit the parser change **and** the regenerated expected CSV in the
   same commit. Splitting them across commits hides the regression
   surface and is grounds for rejecting the PR.

CI does not regenerate. A snapshot mismatch fails the build.

---

## 7. What this plan deliberately does not cover

- **Full-pipeline behaviour.** Owned by [e2e_testing_plan.md](./e2e_testing_plan.md):
  exit codes, default folder names, cache lifecycle across runs, golden
  artifact diffs, determinism.
- **Real-network smoke testing.** Out of scope. Mirror the E2E policy — if
  contract drift becomes a concern, a separate `tests/live/` suite gated on
  `BENCHMARKOOR_TOKEN` and a `--run-live` pytest flag can be added later;
  not part of this plan.
- **Performance benchmarks.** No timing assertions beyond the < 100 ms-per-
  test guideline.
- **Type checking / lint.** `ruff` and the type checker run separately in
  CI; tests don't second-guess them.

---

## 8. CI wiring

- Runs as part of the default `pytest` invocation. No marker, no separate
  job. Same step as E2E.
- Contributes to the line-coverage gate. E2E does not — these tests are
  what the coverage bar is judged against.

---

## 9. Implementation order

Per [implementation_plan.md §13](./implementation_plan.md#13-implementation-order),
unit tests are authored in step 3 — after the skeleton (step 1) and the E2E
scenarios (step 2), before any §11 module exists. Authoring order within
step 3 (keeps the failing suite readable while it grows):

1. `test_config.py` — anchors the data model the rest of the suite depends
   on. Define the YAML shape concretely.
2. `test_titles_parser.py` + the `sample_test_titles.txt` /
   `sample_test_titles_expected.csv` pair — biggest regression surface;
   land the snapshot early.
3. `test_opcount.py` — small, self-contained.
4. `test_client_http.py` — needs `responses` fixtures; bigger time
   investment.
5. `test_cache.py` — depends on the HTTP fetcher signatures being settled.
6. `test_reporter.py` — small, standalone; can be authored any time after
   the Reporter signature is settled. In practice it's added alongside the
   cache + CLI tests so the verbosity surface is locked end-to-end.
7. `test_cli.py` — last; argparse surface stabilises after the rest of the
   API does.

Every file is red at the end of step 3. Implementation step 4a–4f
(§13) turns them green in roughly the same order.
