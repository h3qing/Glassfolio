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

## Early UI + phase 2

- **The UI came before phase 6** (the owner asked, because a CLI-only tool is hard
  to use and to check). The spec's form is kept: a local web page on 127.0.0.1,
  React, later packaged with Tauri.
- **Localhost guards:** the Host header must be ours (defeats DNS rebinding). The
  session cookie is HttpOnly and SameSite=Strict, and can only be set through the
  one-time token link. API requests must come from our own origin. Uploads are
  parsed from memory, so plaintext is never written to a temp file.
- **No web fonts or CDNs.** The UI uses the system SF Pro, so opening the UI makes
  no outbound requests.
- **Slicing** uses nicknames (person, account type, broker, account), and every
  view shares the same `Slice`.

## Phase 3

- **Attribution generalised.** Four look-throughs (see `attribution.py`) give price
  / your money / fund rebalancing. For one fund level this is exactly spec §4.2.
  It also works for nested funds and splits, and the three effects always sum to
  the change. Weight-only funds and index proxies are marked ≈ because their
  "baskets" move with prices.
- **Flows are derived, not stored** (a departure from the spec's `cash_flows` table,
  prompted by code review). An early version worked flows out once, at import time.
  Out-of-order imports, corrected statements, and dividends or prices that arrived
  later then left wrong flows behind. Now each pair of consecutive statements per
  account (a "period") is re-evaluated from current data on every read. Only the
  user's answers (`flow_answers`, keyed by account and period, latest answer wins,
  so answers can be changed) and rules (`flow_rules`) are stored.
- **Flow inference rules.** The first statement of an account is its starting
  balance. The latest import wins for a given date.
  - Below the threshold (max($500, 1% of value)), the difference is `inferred`.
  - Above it, a remembered rule for that account and direction classifies it;
    otherwise it becomes a question.
  - A holding sold between statements with no close after the earlier statement
    is a `data_gap` question. It is never valued at the old price.
  - Dividends count once per security and date.
  - Transfers pair two open questions from different accounts with opposite amounts
    (within 1%) whose dates are within 7 days.
- **Accounts opened mid-period** bring their opening value into returns as money
  added, not as gain.
- **Returns.** Periods start and end on statement dates, and flows are assumed to
  happen on those dates. TWR is cumulative for the period. MWR is annualised
  (XIRR), but periods under a year show the period figure, because annualising a
  short period gives absurd numbers. Open questions are reported alongside, since
  they make returns unreliable.
- **dbt is not used (yet).** The spec planned dbt-duckdb for the incremental
  `portfolio_daily`. It's one model with simple "compute once per day, append"
  semantics, so a small Python function (`snapshots.py`) does it without dbt-core's
  dependency weight. Revisit if the number of models grows.
- **`portfolio_daily.kind`** (added): security, cash, other or fund_residual, so a
  day's rows still add up to the portfolio.
- **Prices from Tiingo.** The token lives in the Keychain and is sent in the
  `Authorization` header, never in the URL or logs. Only `api.tiingo.com` over HTTPS
  is allowed. The free tier is about 50 requests per hour, so each run has a budget:
  held securities and funds first, then look-through constituents by size. The
  others keep the price from their fund's holdings file. Split factors become
  corporate actions, and dividends go to `prices.dividend_per_share`. A daily move
  beyond ±25% that no split explains opens a review question.
- **Repeat fetches** add no duplicate price rows or review questions.
- **Localhost hardening** (from security review):
  - The launch token works once and is exchanged for a separate session secret.
    The cookie is named after the port, because browsers don't separate cookies
    by port.
  - API calls need an `X-Glassfolio: 1` header (which forces a CORS preflight
    that is never approved), and `Sec-Fetch-Site` must be same-origin when present.
  - Pages send `X-Frame-Options: DENY` and a strict CSP.
  - Uploads stay in memory (Starlette's spooling to disk is disabled) and are capped
    at 20 MB before parsing.
  - The Tiingo client refuses redirects, since urllib would follow them to any host
    with the token header.
- **Scheduling** is left to the user (a launchd example is in the README). Glassfolio
  never installs system configuration by itself.
