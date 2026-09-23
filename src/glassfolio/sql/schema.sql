-- All business tables are append-only. The latest row per id wins where a
-- table models mutable state (securities, accounts, owners).
CREATE TABLE IF NOT EXISTS owners (
    owner_id VARCHAR, nickname VARCHAR, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS accounts (
    account_id VARCHAR, nickname VARCHAR, owner_id VARCHAR, broker VARCHAR,
    account_type VARCHAR, currency VARCHAR, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS securities (
    security_id VARCHAR, ticker VARCHAR, name VARCHAR, type VARCHAR,
    cusip VARCHAR, isin VARCHAR, figi VARCHAR, proxy_security_id VARCHAR,
    created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS positions (
    account_id VARCHAR, security_id VARCHAR, shares DECIMAL(24, 8),
    cost_basis DECIMAL(24, 8), export_price DECIMAL(24, 8),
    as_of_date DATE, import_file_hash VARCHAR
);
CREATE TABLE IF NOT EXISTS prices (
    security_id VARCHAR, date DATE, close DECIMAL(24, 8),
    dividend_per_share DECIMAL(24, 8), source VARCHAR
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    security_id VARCHAR, date DATE, type VARCHAR, ratio_or_amount DECIMAL(24, 8)
);
CREATE TABLE IF NOT EXISTS etf_holdings (
    etf_id VARCHAR, holding_id VARCHAR, as_of_date DATE, shares DECIMAL(24, 8),
    weight DOUBLE, etf_shares_outstanding DECIMAL(24, 8), source VARCHAR,
    raw_file_hash VARCHAR, fetched_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS import_profiles (
    profile_id VARCHAR, broker VARCHAR, column_mapping JSON, confirmed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS import_files (
    file_hash VARCHAR, kind VARCHAR, profile_id VARCHAR, account_id VARCHAR,
    as_of_date DATE, imported_at TIMESTAMP, status VARCHAR, raw_content BLOB
);
CREATE TABLE IF NOT EXISTS recon_results (
    check_id VARCHAR, scope VARCHAR, check_type VARCHAR, as_of_date DATE,
    expected DOUBLE, actual DOUBLE, diff DOUBLE, status VARCHAR, hint VARCHAR,
    run_at TIMESTAMP
);
-- Never holds amounts or quantities. snapshot_after is derived from the
-- DuckLake snapshot whose commit_extra_info equals op_id.
CREATE TABLE IF NOT EXISTS ops_log (
    op_id VARCHAR, ts TIMESTAMP, actor VARCHAR, tool VARCHAR, params JSON,
    description VARCHAR, rows_inserted BIGINT, rows_updated BIGINT,
    rows_deleted BIGINT, snapshot_before BIGINT
);
-- Phase 3
-- Cash flows themselves are derived from statements on every read (flows.py);
-- only the user's answers are stored, keyed by account and statement period.
CREATE TABLE IF NOT EXISTS flow_answers (
    item_id VARCHAR, account_id VARCHAR, start_date DATE, end_date DATE,
    classification VARCHAR, paired_item_id VARCHAR, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS flow_rules (
    rule_id VARCHAR, account_id VARCHAR, pattern VARCHAR, classification VARCHAR,
    created_at TIMESTAMP
);
-- Status changes append a new row with the same item_id; the latest wins.
CREATE TABLE IF NOT EXISTS inbox_items (
    item_id VARCHAR, type VARCHAR, payload JSON, status VARCHAR,
    created_at TIMESTAMP, resolved_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS portfolio_daily (
    date DATE, account_id VARCHAR, owner_id VARCHAR, security_id VARCHAR,
    kind VARCHAR, direct_value DOUBLE, via_fund_value DOUBLE, confidence_status VARCHAR,
    computed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fetch_log (
    fetch_id VARCHAR, source VARCHAR, target VARCHAR, started_at TIMESTAMP,
    status VARCHAR, error VARCHAR, raw_file_hash VARCHAR
);
-- Phase 4. Rates are planning assumptions, not tax advice.
CREATE TABLE IF NOT EXISTS tax_profiles (
    tax_profile_id VARCHAR, name VARCHAR, federal_ltcg_rate DOUBLE, federal_ordinary_rate DOUBLE,
    niit BOOLEAN, state VARCHAR, state_rate DOUBLE, withdrawal_rate DOUBLE,
    no_lot_assumption VARCHAR, count_losses BOOLEAN, created_at TIMESTAMP
);
-- Which profile applies to a person or an account (account wins), and an optional
-- per-account tax treatment override (taxable / deferred / exempt). Latest row wins.
CREATE TABLE IF NOT EXISTS tax_assignments (
    scope VARCHAR, scope_id VARCHAR, tax_profile_id VARCHAR, treatment VARCHAR, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS position_lots (
    account_id VARCHAR, security_id VARCHAR, acquired_date DATE, shares DECIMAL(24, 8),
    cost DECIMAL(24, 8), as_of_date DATE, import_file_hash VARCHAR
);
-- Phase 5: a confirmed reading is remembered by the fingerprint of the file's header row.
ALTER TABLE import_profiles ADD COLUMN IF NOT EXISTS kind VARCHAR;
ALTER TABLE import_profiles ADD COLUMN IF NOT EXISTS header_fingerprint VARCHAR;
