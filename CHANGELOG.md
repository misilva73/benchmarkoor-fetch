# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `query.run_type` is renamed to `query.run_id_pattern` and now accepts an
  arbitrary regex that is matched against each `run_id` with `re.fullmatch`
  (the whole `run_id` must match). The previous trailing-segment equality
  check is recoverable by writing `'.*-<value>'`. Malformed patterns raise
  `pydantic.ValidationError` at config load, before any HTTP. The same rename
  applies to the `BenchmarkoorClient.list_runs(run_id_pattern=...)` kwarg and
  to the `query.run_id_pattern` key written into `meta.json`.

### Fixed

- `opcount` is now correctly computed for fixtures whose target opcode is the
  `ECRECOVER` or `P256VERIFY` precompile. Previously these were absent from
  the precompile set, so the trace lookup fell back to a non-existent opcode
  column and silently produced `opcount=0`. They now route through
  `STATICCALL` like the other precompiles.
- `opcount` is now correctly computed for `test_keccak_diff_mem_msg_sizes`
  and any other fixture whose target opcode is `KECCAK256`. `KECCAK256` is
  EVM opcode `0x20`, not a precompile (the precompile at address `0x02` is
  `SHA2-256`), but it was wrongly included in the precompile set. The
  lookup therefore routed through `STATICCALL`, which is unpopulated for
  these tests, and silently produced `opcount=0` even though the trace's
  `KECCAK256` column held millions of operations.
- `BenchmarkoorClient.list_runs` no longer caches its response on disk. The
  underlying listing changes over time as new completed runs accumulate under
  the same `(suite, start_date)` key, so the never-expiring cache silently
  returned stale data and pipeline runs missed any runs added since the cache
  was first populated. `list_runs` now always hits the API, matching suite
  discovery. The per-run `test_stats` parquets and per-suite `summary.json`
  trace caches are genuinely content-addressed and remain unchanged. Existing
  `<cache_dir>/<suite>/runs-from-*.json` and `runs-all.json` files are no
  longer read or written and can be deleted.

### Removed

- `DiskCache.runs_key` helper, now that `list_runs` is uncached.

## [0.1.1] - 2026-05-26

### Added

- MkDocs site with `mkdocstrings`-generated API reference and a quickstart
  Jupyter notebook under `examples/`.
- GitHub Actions workflow to deploy docs from `main`.

### Changed

- README expanded with installation and usage details.

## [0.1.0] - 2026-05-26

Initial release.

### Added

- `BenchmarkoorClient` with `resolve_suite`, `list_suites`, `list_runs`,
  `fetch_test_stats`, `fetch_trace`, `parse`, and end-to-end `run`.
- `FetchConfig` (pydantic v2) loaded from YAML, with CLI overrides for any
  `query.*` / `output.*` field.
- `FetchResult` with `bench_df`, `trace_df`, and `write()` for the standard
  artifact bundle (`runtimes.csv`, `opcounts.json`, `bench_data.parquet`,
  `trace.parquet`, `meta.json`).
- `parse_test_titles` for the current Benchmarkoor test-title shape, with
  unparsed titles surfaced as a single end-of-run warning and recorded
  under `unparsed_fixtures` in `meta.json`.
- Content-addressed on-disk cache, never-expiring by default; suite
  discovery is intentionally uncached.
- CLI: `benchmarkoor-fetch run` and `benchmarkoor-fetch suites`, with
  `--verbose` / `--quiet`, `--no-cache`, `--cache-dir`, and per-field
  overrides. Exit codes: `0` success, `1` config/input error, `2` HTTP
  error, `3` empty result.

[Unreleased]: https://github.com/misilva73/benchmarkoor-fetch/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/misilva73/benchmarkoor-fetch/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/misilva73/benchmarkoor-fetch/releases/tag/v0.1.0
