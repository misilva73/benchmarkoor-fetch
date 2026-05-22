# `benchmarkoor-fetch` — End-to-End Testing Plan

Complements [§13 of the implementation plan](./implementation_plan.md), which
already covers per-module unit tests. This document covers only **end-to-end**
tests: the full pipeline driven through its two public surfaces (CLI and
`BenchmarkoorClient`), against a mocked Benchmarkoor API.

---

## 1. Goal

Catch regressions that unit tests can't see:

- A correctly-tested HTTP client + a correctly-tested parser can still produce
  the wrong artifact bundle if the pipeline glues them together wrong.
- The CLI / library API are themselves behaviours, not implementation details:
  exit codes, default `--out` folder name, env-var fallback for the token,
  and CLI overrides need their own coverage.
- Caching is a stateful, cross-run behaviour: only an E2E test sees that the
  second run produces identical outputs **without** any HTTP traffic.

Non-goals: re-testing pure functions (parser, opcount, retry policy) that
already have unit coverage in §13. E2E asserts the *seams*, not the cells.

---

## 2. Test policy

- **Hermetic.** All HTTP is mocked with [`responses`](https://github.com/getsentry/responses)
  (already listed in §12 dev deps). No network access in the test process; CI
  runs with no secrets.
- **Stateless filesystem.** All tests use `tmp_path` for the cache dir and the
  output dir. No `~/.cache/benchmarkoor-fetch` writes leak out of the test.
- **Golden-file diff.** Each artifact has a committed expected version under
  `tests/data/e2e/golden_outputs/`. Tests load that, normalise (sort rows where
  order is incidental, drop `fetched_at`-style timestamps), and compare.
- **One canonical fixture suite.** A single `tests/data/e2e/responses/` folder
  holds the mocked API responses (multi-page, multi-suite, multi-run). Every
  E2E test points at that same fixture set unless it specifically needs a
  variant (auth-failure, empty-result, retry-then-succeed, etc.).
- **No retesting of internals.** If a test would pass even with the pipeline
  glue removed, it belongs in a unit test, not here.

---

## 3. Fixture layout

```
tests/data/e2e/
├── fetch.yaml                     # canonical config used by most E2E tests
├── responses/
│   ├── suites.json                # /suites — multiple matching, latest wins
│   ├── runs.json                  # /runs?suite_hash=… — 3 runs, varying ts
│   ├── test_stats_page1.json      # /test_stats — page 1 of 3
│   ├── test_stats_page2.json
│   ├── test_stats_page3.json
│   └── summary.json               # /files/.../summary.json — opcode counts
├── variants/
│   ├── runs_empty.json            # zero runs → exit 3
│   ├── auth_401.json              # → exit 2
│   ├── test_stats_502_then_200/   # retry-then-succeed sequence
│   ├── test_stats_one_empty/      # 3 runs, one paginates to total=0 (scenario 9b)
│   ├── unparsed_titles.json       # titles the parser can't match → warning
│   └── unparsed_titles_15.json    # 15 unparsed titles → truncated stderr (9a)
└── golden_outputs/
    ├── runtimes.csv
    ├── opcounts.json
    ├── bench_data.parquet
    ├── trace.parquet
    └── meta.json                  # fetched_at / package_version stripped at compare time
```

A `conftest.py` at `tests/e2e/conftest.py` wires `responses.add()` once per
test from those files and exposes a `mocked_api` fixture. The golden bundle
is the source of truth — regenerating it is a deliberate, reviewed action
(see [§6](#6-regenerating-golden-files)).

---

## 4. Test file layout

```
tests/e2e/
├── conftest.py                 # mocked_api, tmp cache dir, tmp out dir, runner
├── test_cli_run.py             # `benchmarkoor-fetch run` happy path + overrides
├── test_cli_suites.py          # `benchmarkoor-fetch suites` discovery subcommand
├── test_library_api.py         # BenchmarkoorClient — Style A + Style B
├── test_outputs.py             # per-artifact schema + content (golden diff)
├── test_cache_lifecycle.py     # cold → warm → --no-cache
└── test_errors.py              # exit codes, auth, empty result, bad config
```

---

## 5. Scenarios

Each row is one test. "Asserts" is the *minimum* assertion set — tests may
check more, but at least these must hold.

### 5.1 CLI — `test_cli_run.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 1 | Happy path: `run --config fetch.yaml --out tmp/data` against canonical fixtures | exit 0; all 5 artifacts present under `tmp/data/`; each diffs clean against `golden_outputs/`. |
| 2 | Default `--out` derives from data window | `tmp/{earliest_run_ts}_{latest_run_ts}/` exists, format `YYYY-MM-DDTHH-MM-SSZ`, reflects actual `start_ts`/`end_ts` of fetched runs (not `query.start_date`). |
| 3 | CLI overrides win over YAML | `--fork osaka` while YAML says `amsterdam` → `/suites` is called with `fork=osaka`; `meta.json.query.fork == "osaka"`. |
| 4 | `--token <X>` overrides `BENCHMARKOOR_TOKEN` env | Captured `Authorization` header is `Bearer <X>`. (Env-only fallback at the library layer is covered by unit #43a — no separate E2E.) |
| 6 | Output-flag combinations | Parametrized over the meaningful combinations: each of `estimator_inputs` / `merged_parquet` / `trace_parquet` false in isolation, and all three false. For each: only the disabled artifacts are absent; `meta.json` always present; exit 0. |
| 8 | `--verbose` enables progress + `miss: <key>` lines | stderr contains `miss:` on cold run; silent on warm run. |
| 9 | Unparsed titles emit a warning and land in meta | stderr contains `WARN: N unparsed fixtures`; `meta.json.unparsed_fixtures` lists them. Run still exits 0. |
| 9a | Unparsed-fixture warning truncates at 10 | Fixture set crafted with 15 unparsed titles → stderr line reads `WARN: 15 unparsed fixtures: <10 names>, …` (10 names, ellipsis, total count). `meta.json.unparsed_fixtures` contains all 15. Locks the §7 truncation rule. |
| 9b | One run_id returns zero `/test_stats` rows | Fixture variant: 3 runs, one of which paginates to `total=0`. Pipeline completes; `bench_data.parquet` contains rows from the other two runs only; `meta.json.row_counts` reflects the actual count; exit 0. Catches empty-DataFrame concat regressions. |

### 5.2 CLI — `test_cli_suites.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 10 | `suites --network … --fork … --test-type …` | Prints resolved `suite_hash` + `indexed_at` to stdout; no `/runs` or `/test_stats` calls made; exit 0. |
| 11 | Discovery resolves "latest matching" deterministically | With two indexed suites matching the tuple, the printed hash is the one with the later `indexed_at`. |

### 5.3 Library — `test_library_api.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 12 | Style A: `client.run(config)` returns a `FetchResult` | `result.bench_df` and `result.trace_df` are pandas DataFrames with the §4 schemas; row counts match golden parquets. |
| 13 | `FetchResult.write(tmp)` produces same bundle as the CLI | Diff each artifact against `golden_outputs/`. Asserts CLI and library share the writer. |
| 14 | Style B granular path: resolve → list_runs → fetch_test_stats → fetch_trace → parse | Intermediate values match the corresponding response fixtures; final `bench_df`/`trace_df` equal Style A's output. |
| 15 | `BenchmarkoorClient(token=…)` constructor wins over env | Captured `Authorization` matches constructor arg even when env var is also set. |
| 16 | `query.suites: [hash1, hash2]` skips discovery | No `/suites` call recorded; both hashes appear in `meta.json.suites`. |

### 5.4 Outputs — `test_outputs.py`

Schemas and row content of `runtimes.csv`, `opcounts.json`, and `bench_data.parquet`
are already locked by the golden-bundle diff in scenario #1. The two tests below
cover artifact-level claims the golden diff doesn't make on its own.

| # | Scenario | Asserts |
| --- | --- | --- |
| 20 | `trace.parquet` is derived, not fetched | After loading the canonical fixtures, `summary.json` is requested exactly once; `trace.parquet` content equals projection of `opcounts.json`. |
| 21 | `meta.json` dynamic fields | Beyond the golden diff: `fetched_at` present and ISO-8601, `package_version` matches the installed package, `data_window: {start, end}` matches the *actual* `earliest_run_ts`/`latest_run_ts` (not `query.start_date`/`end_date`), and `unparsed_fixtures` reflects the run. These fields are stripped/normalised before the #1 golden diff, so they need their own assertions. |

### 5.5 Cache — `test_cache_lifecycle.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 22 | Cold run populates cache | After run 1: cache contains `{suite_hash}/runs/…json`, `{suite_hash}/test_stats/{run_id}.parquet` for each run_id, `{suite_hash}/summary.json`. Exact paths match §9.1. |
| 23 | Warm run makes zero HTTP calls **except discovery** | `responses` is configured with `assert_all_requests_are_fired=False` and the test asserts only `/suites` was called on run 2. `runtimes.csv` byte-equals run 1's. |
| 24 | Warm run derives default `--out` without network | Even with no `runs`/`test_stats` calls, the timestamp folder name on run 2 equals run 1's (cache stores raw responses with `start_ts`/`end_ts`). |
| 25 | `--no-cache` bypasses reads and writes | Run with `--no-cache` after a warm run still hits all endpoints; cache directory mtime unchanged. (`cache.enabled: false` in YAML takes the same code path — covered at the unit layer.) |
| 28 | Different `(start_date, end_date)` windows do not share a runs-cache entry | Run twice with different windows; two separate `runs/…json` files exist in cache. |

### 5.6 Errors and exit codes — `test_errors.py`

| # | Scenario | Asserts |
| --- | --- | --- |
| 31 | Missing token (no `--token`, no env) | Exit 1; stderr mentions `BENCHMARKOOR_TOKEN`. |
| 32 | Invalid YAML (`fork:` missing) | Exit 1; stderr names the missing field (pydantic message). |
| 33 | `/suites` returns 401 | Exit 2; stderr distinguishes auth failure from generic 5xx. |
| 34 | `/test_stats` returns 502 three times in a row | Exit 2 after retry budget; stderr shows attempted retries. |
| 35 | `/test_stats` returns 502 twice then 200 | Exit 0; full pipeline completes; matches `retries: 3, backoff_factor: 2` from config. |
| 36 | `/runs` returns an empty list | Exit 3; no output files written; stderr says "no runs matched window". |
| 37 | Unknown `suite_hash` in explicit `query.suites:` returns 404 on `/runs` | Exit 2; error names the offending hash. |

Determinism is covered implicitly: scenario #23 already asserts the warm
run's `runtimes.csv` byte-equals the cold run's, which is the
"cache-returns-same-bytes-as-HTTP" claim. A separate cross-run byte-equality
test was dropped — parquet byte-identity is brittle across `pyarrow` versions
and writer-option drift, and content equality on the loaded DataFrames is
what we actually care about (and what #23 already asserts).

---

## 6. Regenerating golden files

When the schema legitimately changes (new column in `bench_data.parquet`, new
key in `meta.json`, etc.) the golden bundle has to move with it. The
regeneration flow is **explicit, not implicit**:

1. `pytest tests/e2e/ --regenerate-golden` (a CLI flag added to `conftest.py`)
   reruns the pipeline against the fixture API responses and overwrites
   `tests/data/e2e/golden_outputs/`.
2. The diff is reviewed manually as part of the PR.
3. CI does **not** pass `--regenerate-golden`. A golden mismatch in CI fails
   the build.

The mocked API responses under `tests/data/e2e/responses/` are *also* committed
and reviewed. They are not auto-regenerated from the real API — a separate
manual script `scripts/refresh_e2e_fixtures.py` lets a developer pull a fresh
snapshot when the Benchmarkoor API contract changes, but that is a deliberate
maintenance task, not a test action.

---

## 7. What this plan deliberately does not cover

- **Unit-level parser correctness.** Already covered by `tests/test_titles_parser.py`
  (the snapshot test in §7 of the implementation plan). E2E only checks that
  parser output reaches the artifacts intact.
- **Unit-level retry/backoff timing.** Covered by `tests/test_client_http.py`.
  E2E only checks the user-visible outcome (retry-then-succeed → exit 0;
  exhausted retries → exit 2).
- **Pydantic field validation.** One representative case (#32) is enough; full
  matrix lives in `tests/test_config.py`.
- **Real-network smoke testing.** Out of scope per [§2](#2-test-policy). If
  contract drift becomes a concern, add a separate `tests/live/` suite gated
  on `BENCHMARKOOR_TOKEN` and a `--run-live` pytest flag — not part of this
  plan.

---

## 8. CI wiring

- E2E runs as part of the default `pytest` invocation in CI. No separate job,
  no separate marker — fast enough (everything in-process, all mocked) to live
  in the main test step.
- Add `pytest -m "not e2e" tests/` as an optional fast-path for local
  iteration, but no test is *only* marked `e2e`; the marker is additive.
- Coverage gate: E2E does not contribute to the line-coverage gate (unit tests
  do). E2E is judged by its scenario count, not by lines hit.

---

## 9. Implementation order

Tracks [implementation_plan.md §13](./implementation_plan.md#13-implementation-order).
E2E **leads** the work, alongside the [unit testing plan](./unit_testing_plan.md):
the fixture bundle and every scenario in §5 are authored before any module
under [implementation_plan.md §11](./implementation_plan.md#11-package-layout)
exists. They all fail at first — that's the executable spec for §13's
implementation steps.

- §13 step 2 commits all E2E scenarios. They are red.
- §13 step 4a–4f turn them green in dependency order: config → client →
  cache → parser → pipeline → CLI.
- By the end of step 4f, every scenario in §5 is green and the suite is
  shippable.

Recommended authoring order within §13 step 2 (to keep the failing suite
readable while writing it):

1. Scenarios **#1, #12, #17–#21** — the canonical happy path + artifact
   schemas. Establishes the fixture shape and the golden bundle.
2. Scenarios **#3–#11** — CLI surfaces, overrides, default `--out`.
3. Scenarios **#22–#30** — cache lifecycle. Requires `tmp_path` cache dir
   plumbing in `conftest.py`.
4. Scenarios **#31–#37** — error paths and exit codes. Add the `variants/`
   fixture subtree here.
