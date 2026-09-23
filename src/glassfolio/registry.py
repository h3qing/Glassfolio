"""Owners, accounts, import profiles and security proxies.

Accounts are identified by nickname only; account numbers are never stored.
"""

import json
from dataclasses import dataclass, replace

import duckdb

from glassfolio.lake import Lake, OpMeta, RowCounts, new_id, run_write, utc_now
from glassfolio.securities import Security, insert_security, load_securities, resolve

ACCOUNT_TYPES = ("taxable", "traditional_ira", "roth_ira", "401k", "hsa", "529", "other")


@dataclass(frozen=True)
class Account:
    account_id: str
    nickname: str
    owner_id: str
    broker: str
    account_type: str
    currency: str


def _latest(table: str, key: str) -> str:
    return f"""SELECT * EXCLUDE (rn, created_at) FROM (SELECT *, row_number() OVER (
        PARTITION BY {key} ORDER BY created_at DESC) AS rn FROM {table}) WHERE rn = 1"""


def find_owner(con: duckdb.DuckDBPyConnection, nickname: str) -> str | None:
    row = con.execute(
        f"SELECT owner_id FROM ({_latest('owners', 'owner_id')}) WHERE nickname = ?",
        [nickname],
    ).fetchone()
    return row[0] if row else None


def find_account(con: duckdb.DuckDBPyConnection, nickname: str) -> Account | None:
    row = con.execute(
        f"SELECT * FROM ({_latest('accounts', 'account_id')}) WHERE nickname = ?",
        [nickname],
    ).fetchone()
    return Account(*row) if row else None


def add_owner(lake: Lake, nickname: str, actor: str = "user") -> str:
    if find_owner(lake.con, nickname):
        raise ValueError(f"owner already exists: {nickname}")
    owner_id = new_id("own")

    def work(con):
        con.execute("INSERT INTO owners VALUES (?, ?, ?)", [owner_id, nickname, utc_now()])
        return owner_id, RowCounts(inserted=1)

    meta = OpMeta(actor, "add_owner", {"owner_id": owner_id}, "add owner")
    return run_write(lake, meta, work)[0]


def add_account(
    lake: Lake, nickname: str, owner: str, broker: str, account_type: str,
    currency: str = "USD", actor: str = "user",
) -> str:
    if account_type not in ACCOUNT_TYPES:
        raise ValueError(f"account_type must be one of {ACCOUNT_TYPES}")
    if find_account(lake.con, nickname):
        raise ValueError(f"account already exists: {nickname}")
    owner_id = find_owner(lake.con, owner)
    if owner_id is None:
        raise ValueError(f"unknown owner: {owner}")
    account_id = new_id("acct")

    def work(con):
        con.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [account_id, nickname, owner_id, broker, account_type, currency, utc_now()],
        )
        return account_id, RowCounts(inserted=1)

    meta = OpMeta(actor, "add_account", {"account_id": account_id}, "add account")
    return run_write(lake, meta, work)[0]


def add_profile(lake: Lake, broker: str, mapping: dict, actor: str = "user",
                kind: str = "positions", header_fingerprint: str | None = None) -> str:
    """Save a user-confirmed column mapping (configuration, never code)."""
    profile_id = new_id("prof")

    def work(con):
        con.execute(
            "INSERT INTO import_profiles (profile_id, broker, column_mapping, confirmed_at, kind, "
            "header_fingerprint) VALUES (?, ?, ?, ?, ?, ?)",
            [profile_id, broker, json.dumps(mapping), utc_now(), kind, header_fingerprint],
        )
        return profile_id, RowCounts(inserted=1)

    meta = OpMeta(actor, "add_profile", {"profile_id": profile_id, "broker": broker},
                  "save import profile")
    return run_write(lake, meta, work)[0]


def load_profile(con: duckdb.DuckDBPyConnection, profile_id: str) -> dict:
    row = con.execute(
        "SELECT column_mapping FROM import_profiles WHERE profile_id = ?", [profile_id]
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown profile: {profile_id}")
    return json.loads(row[0])


def _by_ticker(master: tuple[Security, ...], ticker: str) -> Security:
    sec = resolve(master, Security(None, ticker, None, "other"))
    if sec is None:
        raise ValueError(f"unknown or ambiguous ticker: {ticker}")
    return sec


def set_proxy(lake: Lake, ticker: str, proxy_ticker: str, actor: str = "user") -> None:
    """Map a fund without holdings (e.g. a CIT) to an ETF tracking the same index."""
    master = load_securities(lake.con)
    sec = _by_ticker(master, ticker)
    proxy = _by_ticker(master, proxy_ticker)

    def work(con):
        insert_security(con, replace(sec, proxy_security_id=proxy.security_id))
        return None, RowCounts(inserted=1)

    meta = OpMeta(actor, "set_proxy", {"security_id": sec.security_id,
                                       "proxy_security_id": proxy.security_id},
                  "map fund to index proxy")
    run_write(lake, meta, work)


@dataclass(frozen=True)
class AccountInfo:
    nickname: str
    owner: str
    account_type: str
    broker: str
    currency: str
    latest_statement: object  # date | None


def list_accounts(con: duckdb.DuckDBPyConnection) -> tuple[AccountInfo, ...]:
    rows = con.execute(f"""
        SELECT a.nickname, o.nickname, a.account_type, a.broker, a.currency,
            (SELECT max(f.as_of_date) FROM import_files f
             WHERE f.account_id = a.account_id AND f.kind = 'statement' AND f.status = 'imported')
        FROM ({_latest('accounts', 'account_id')}) a
        LEFT JOIN ({_latest('owners', 'owner_id')}) o USING (owner_id)
        ORDER BY 2, 1""").fetchall()
    return tuple(AccountInfo(*r) for r in rows)


def list_owners(con: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
    rows = con.execute(f"SELECT nickname FROM ({_latest('owners', 'owner_id')}) ORDER BY 1")
    return tuple(r[0] for r in rows.fetchall())


def list_profiles(con: duckdb.DuckDBPyConnection) -> tuple[tuple[str, str], ...]:
    """(profile_id, broker), newest first."""
    rows = con.execute("SELECT profile_id, broker FROM import_profiles ORDER BY confirmed_at DESC")
    return tuple((r[0], r[1]) for r in rows.fetchall())
