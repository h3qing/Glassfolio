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
