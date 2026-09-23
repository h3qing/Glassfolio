# Implementation decisions

Choices made while building, including where the code departs from or refines
[SPEC.md](SPEC.md).

## Phase 1 (MVP)

- **DuckLake + encryption verified.** The DuckLake catalog is an encrypted DuckDB
  file (`METADATA_PARAMETERS MAP {'ENCRYPTION_KEY': …}`), and data files use `ENCRYPTED`.
  A wrong key fails to attach, Parquet files can't be read on their own, and small
  inserts are inlined into the encrypted catalog. Tested with DuckDB 1.5.5.
- **ops_log ↔ snapshot link.** Each write runs in one transaction that also appends
  its `ops_log` row and calls `set_commit_message(..., extra_info => op_id)`.
  `snapshot_after` is derived from the snapshot tagged with the op_id, so the log
  stays append-only.
- **Restore gotcha.** Inside a transaction, a DuckLake `AT (VERSION => n)` read issued
  after a `DELETE` on the same table returns nothing. `restore` therefore stages the old
  rows in a temp table before deleting. Covered by `test_restore_rolls_back_an_import`.
- **`positions.export_price`** (not in the spec's field list). Spec §11 says to
  reconcile with the export's prices and analyse with closes. Export prices are also
  written to `prices` with source `broker_export`, ranked below real closes and
  ETF-file prices on the same date.
- **`import_files`** gains `kind`, `as_of_date` and `raw_content`. The original file is
  kept inside the encrypted lake (spec §5.1.7), so the user can delete their copy.
- **Exposure output** also has `position` rows (top-level holdings) and `fund_residual`
  rows, so conservation can be checked from a single query.
- **Security identity.** IDs are opaque (`sec_…`). Matching tries FIGI, then ISIN, then
  CUSIP, and uses the ticker only when no stable identifier conflicts. OpenFIGI mapping
  comes later.
- **New securities default to `stock`.** Importing an ETF's holdings promotes it to
  `etf`. An ETF you hold without importing its holdings shows up as a single
  "company" until you import them.
- **Network:** besides market and ETF data sources, the allowlist must include
  `extensions.duckdb.org`, because the first run installs the `ducklake` extension.
- **Raw files:** the original statement is stored inside the encrypted lake. The
  plaintext copy is deleted only when you pass `--delete-source`, since deleting your
  files is never automatic.
- **`GLASSFOLIO_DB_KEY`** is for tests and development only. Environment variables can
  leak through shell history and process info, so real keys stay in the Keychain.
- **Untrusted text** from import files is stripped of control characters before
  printing, so a doctored file can't use terminal escape codes to fake the
  confirmation preview.
- **Look-through edge cases** (from code review, each covered by
  `tests/test_review_regressions.py`):
  - The same security on two statement rows is summed, and so is the same holding on
    two rows of a fund file.
  - A holdings version is identified by (etf, as_of, fetched_at), not by file hash, and
    re-importing a file with the same hash is refused.
  - Non-USD cash and non-equity rows (futures, swaps) are typed `other` and carried in
    dollars as `weight × p(fund)`. Futures contract counts would give notional value, so
    they are not used.
  - A fund that lists itself as a holding: the self-edge is dropped and that slice stays
    in the fund residual.
  - Duplicate corporate actions are ignored, both at import and in the query.
  - Duplicate and validation checks run again inside the write transaction, so a
    stale preview can't commit.
- **Not yet:** dbt (arrives with the incremental `portfolio_daily` in phase 3), network
  fetching of issuer files (`refresh_etf_holdings`), `inbox_items`, and the MCP
  server. The importers are already split into preview and commit, the shape those
  tools need.
