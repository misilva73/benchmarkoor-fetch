# benchmarkoor-fetch

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

## Where to look next

- **[Quickstart notebook](https://github.com/misilva73/benchmarkoor-fetch/blob/main/examples/quickstart.ipynb)** — runnable end-to-end walkthrough of the granular Style B API.
- **[README](https://github.com/misilva73/benchmarkoor-fetch/blob/main/README.md)** — CLI usage, YAML config schema, output format, caching.
- **API reference** — full signatures for the four public symbols:
    - [`BenchmarkoorClient`](api/client.md)
    - [`FetchConfig`](api/config.md)
    - [`FetchResult`](api/result.md)
    - [`parse_test_titles`](api/parse.md)
- **[Changelog](changelog.md)** — version history.
