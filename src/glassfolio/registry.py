"""Owners, accounts, import profiles and security proxies.

Accounts are identified by nickname only; account numbers are never stored.
"""

import json
from dataclasses import dataclass, replace

import duckdb

from glassfolio.lake import Lake, OpMeta, RowCounts, new_id, run_write, utc_now
from glassfolio.securities import Security, insert_security, load_securities, resolve

ACCOUNT_TYPES = ("taxable", "traditional_ira", "roth_ira", "401k", "other")


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


def add_profile(lake: Lake, broker: str, mapping: dict, actor: str = "user") -> str:
    """Save a user-confirmed column mapping (configuration, never code)."""
    profile_id = new_id("prof")

    def work(con):
        con.execute(
            "INSERT INTO import_profiles VALUES (?, ?, ?, ?)",
            [profile_id, broker, json.dumps(mapping), utc_now()],
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
