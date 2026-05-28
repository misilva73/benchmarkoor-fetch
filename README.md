# benchmarkoor-fetch

[![PyPI version](https://img.shields.io/pypi/v/benchmarkoor-fetch.svg)](https://pypi.org/project/benchmarkoor-fetch/)
[![Python versions](https://img.shields.io/pypi/pyversions/benchmarkoor-fetch.svg)](https://pypi.org/project/benchmarkoor-fetch/)
[![CI](https://github.com/misilva73/benchmarkoor-fetch/actions/workflows/ci.yml/badge.svg)](https://github.com/misilva73/benchmarkoor-fetch/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://misilva73.github.io/benchmarkoor-fetch/)
[![License: CC0-1.0](https://img.shields.io/badge/license-CC0--1.0-lightgrey.svg)](LICENSE)

Fetch EVM benchmark suites from the [Benchmarkoor](https://benchmarkoor-api.core.ethpandaops.io)
API and write clean tabular outputs (`runtimes.csv`, `opcounts.json`,
`bench_data.parquet`, `trace.parquet`, `meta.json`).

Data-ingestion only — no modelling, no gas analysis. The artifact bundle
feeds [evm-gasfit](https://github.com/misilva73/evm-gasfit), which owns the
gas-cost estimation step.

## Install

```bash
pip install benchmarkoor-fetch
```

Requires Python ≥ 3.11.

## Auth

The Benchmarkoor API requires a bearer token. Set it once:

```bash
export BENCHMARKOOR_TOKEN=...
```

You can also pass it per-call: `--token …` on the CLI, or
`BenchmarkoorClient(token=…)` in Python. It is never read from the YAML
config.

## CLI quickstart

Write a `fetch.yaml`:

```yaml
query:
  network: jochemnet
  fork: amsterdam
  test_type: compute
  start_date: "2026-05-18"
  end_date: "2026-05-20"
```

`fork` is always required. `network` and `test_type` are used to discover
the latest matching `suite_hash`; if you already know the hashes, list them
under `suites:` instead and omit `network` / `test_type`:

```yaml
query:
  fork: amsterdam
  suites:
    - 0xaaa111
    - 0xbbb222
```

To keep only the runs whose `run_id` matches a regex, add `run_id_pattern`:

```yaml
query:
  fork: amsterdam
  suites:
    - 0xaaa111
  run_id_pattern: '.*-full'   # keep runs whose run_id ends with `-full`
```

`run_id_pattern` is matched against each `run_id` with `re.fullmatch`, so the
whole `run_id` must match — use `.*…*` to get substring behaviour (e.g.
`'.*bal-full.*'` to keep any `run_id` containing `bal-full`). A malformed
regex is rejected at config load.

Then run:

```bash
benchmarkoor-fetch run --config fetch.yaml --out ./data
```

If `--out` is omitted, outputs land in
`./{earliest_run_ts}_{latest_run_ts}/` using the actual run timestamps of
the data fetched (not the configured window).

Any `query.*` or `output.*` field can be overridden on the command line:

```bash
benchmarkoor-fetch run --config fetch.yaml \
    --fork osaka --start-date 2026-05-01 --end-date 2026-05-08 \
    --out ./data
```

Resolve the latest suite hash for a (network, fork, test_type) tuple
without fetching the bundle:

```bash
benchmarkoor-fetch suites --network jochemnet --fork amsterdam --test-type compute
```

**Exit codes:** `0` success · `1` config/input error · `2` HTTP error ·
`3` empty result.

**Verbosity:** by default the CLI prints high-level milestones (suite
resolution, runs listed, trace fetch) and a `tqdm` progress bar while
fetching test stats. Pass `--verbose` to also emit per-event detail (cache
hit/miss, per-run fetch lines), or `--quiet` to suppress milestones and
progress; only warnings and errors are shown. `--verbose` and `--quiet`
are mutually exclusive.

## Python / notebook quickstart

Style A — config-driven, full pipeline:

```python
from benchmarkoor_fetch import BenchmarkoorClient, FetchConfig

config = FetchConfig.from_yaml("fetch.yaml")
client = BenchmarkoorClient()           # picks up BENCHMARKOOR_TOKEN
result = client.run(config)             # returns FetchResult
result.bench_df                         # merged DataFrame
result.trace_df                         # per-fixture opcode counts
result.write("./data")                  # writes the artifact bundle
```

Style B — granular, for notebooks that want to inspect mid-pipeline (see
[examples/quickstart.ipynb](examples/quickstart.ipynb) for the runnable
version):

```python
client = BenchmarkoorClient()
suite_hash = client.resolve_suite(
    network="jochemnet", fork="amsterdam", test_type="compute"
)
runs = client.list_runs(suite_hash, start_date="2026-05-18", end_date="2026-05-20")
raw_df = client.fetch_test_stats(runs, suite_hash=suite_hash)
trace = client.fetch_trace(suite_hash)
bench_df, trace_df = client.parse(raw_df, trace, fork="amsterdam")
```

## Outputs

Written to `--out`:

| File | Contents |
| --- | --- |
| `runtimes.csv` | `client_name, fixture_name, test_runtime_ms` — one row per benchmark run. |
| `opcounts.json` | `{fixture_name: {opcount, OPCODE: count, …}}`. |
| `bench_data.parquet` | The full merged frame: `run_id, client_name, test_title, test_file, test_name, test_opcode, test_params, test_runtime_ms, ingestion_timestamp, block_limit_million, opcount`. |
| `trace.parquet` | Per-fixture trace keyed by `test_title`. |
| `meta.json` | Resolved suite hashes, query block, fetched-at timestamp, package version, row counts. |

Runtimes are kept in **milliseconds** (`test_runtime_ms`) on the wire and
on disk — converting to seconds is left to the consumer.

## Feeding evm-gasfit

The two-step workflow is:

```bash
# 1. Ingest with benchmarkoor-fetch
benchmarkoor-fetch run --config fetch.yaml --out ./data

# 2. Estimate gas costs with evm-gasfit, pointing at the same folder
evm-gasfit fit --inputs ./data ...
```

`evm-gasfit` consumes `runtimes.csv` + `opcounts.json` from the output
directory; `bench_data.parquet` and `trace.parquet` are there for direct
DataFrame analysis.

## Caching

Responses are cached on disk under `./.cache/benchmarkoor-fetch/`
(relative to the current working directory) by default. The cache is **content-addressed and never expires** — keys are
built so that a hit guarantees byte-identical bytes. Suite discovery is
intentionally not cached (it must always reflect the latest suite).

Bypass per-run with `--no-cache`, relocate with `--cache-dir <path>`, or
disable in YAML via `cache.enabled: false`.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
ruff format src tests
```

## License

See [LICENSE](LICENSE).
