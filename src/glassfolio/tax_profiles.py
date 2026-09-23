"""Tax profiles (per person, overridable per account), treatments and lots."""

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from glassfolio.lake import Lake, OpMeta, RowCounts, insert_rows, new_id, run_write, utc_now
from decimal import Decimal

from glassfolio.parsing import (cell, clean, file_hash, looks_numeric, parse_date, parse_number, read_rows,
                                read_source)
from glassfolio.registry import find_account, find_owner
from glassfolio.securities import Security, load_securities, resolve

TREATMENTS = ("taxable", "deferred", "exempt")
LOT_ASSUMPTIONS = ("short_term", "long_term")
_DEFAULT_TREATMENT = {"taxable": "taxable", "traditional_ira": "deferred", "401k": "deferred",
                      "roth_ira": "exempt", "hsa": "exempt", "529": "exempt", "other": "taxable"}


def default_treatment(account_type: str) -> str:
    return _DEFAULT_TREATMENT.get(account_type, "taxable")


@dataclass(frozen=True)
class TaxProfile:
    name: str
    federal_ltcg_rate: float
    federal_ordinary_rate: float
    niit: bool
    state: str
    state_rate: float
    withdrawal_rate: float | None = None      # override; default ordinary + state
    no_lot_assumption: str = "short_term"     # used when the export has no lot details
    count_losses: bool = True                 # unrealized losses reduce tax (spec formula)
    tax_profile_id: str | None = None


# California default (spec: "default rates refer to California").
DEFAULT_PROFILE = TaxProfile("California default", 0.15, 0.24, False, "CA", 0.093,
                             tax_profile_id="default")
NIIT_RATE = 0.038


@dataclass(frozen=True)
class Rates:
    ltcg: float
    stcg: float
    withdrawal: float


def effective_rates(p: TaxProfile) -> Rates:
    niit = NIIT_RATE if p.niit else 0.0
    withdrawal = p.withdrawal_rate if p.withdrawal_rate is not None else p.federal_ordinary_rate + p.state_rate
    return Rates(p.federal_ltcg_rate + niit + p.state_rate,
                 p.federal_ordinary_rate + niit + p.state_rate, withdrawal)


def validate_profile(p: TaxProfile) -> TaxProfile:
    rates = (p.federal_ltcg_rate, p.federal_ordinary_rate, p.state_rate, p.withdrawal_rate or 0)
    if any(not isinstance(r, (int, float)) or not 0 <= r < 1 for r in rates):
        raise ValueError("rates are fractions between 0 and 1 (e.g. 0.093 for 9.3%)")
    if p.no_lot_assumption not in LOT_ASSUMPTIONS:
        raise ValueError(f"no_lot_assumption must be one of {LOT_ASSUMPTIONS}")
    if not isinstance(p.niit, bool) or not isinstance(p.count_losses, bool):
        raise ValueError("niit and count_losses must be true or false")
    return p


def add_tax_profile(lake: Lake, profile: TaxProfile, actor: str = "user") -> str:
    validate_profile(profile)
    pid = profile.tax_profile_id if profile.tax_profile_id not in (None, "default") else new_id("tax")
    p = replace(profile, tax_profile_id=pid)

    def work(con):
        con.execute("INSERT INTO tax_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [pid, p.name, p.federal_ltcg_rate, p.federal_ordinary_rate, p.niit, p.state,
                     p.state_rate, p.withdrawal_rate, p.no_lot_assumption, p.count_losses, utc_now()])
        return pid, RowCounts(inserted=1)

    return run_write(lake, OpMeta(actor, "save_tax_profile", {"tax_profile_id": pid},
                                  "save tax assumptions"), work)[0]


def load_profiles(con) -> dict[str, TaxProfile]:
    rows = con.execute("""SELECT * EXCLUDE (created_at) FROM tax_profiles QUALIFY row_number() OVER (
        PARTITION BY tax_profile_id ORDER BY created_at DESC) = 1""").fetchall()
    return {r[0]: TaxProfile(*r[1:], tax_profile_id=r[0]) for r in rows}


def _assign(lake: Lake, scope: str, scope_id: str, profile_id: str | None, treatment: str | None,
            actor: str) -> None:
    def work(con):
        con.execute("INSERT INTO tax_assignments VALUES (?, ?, ?, ?, ?)",
                    [scope, scope_id, profile_id, treatment, utc_now()])
        return None, RowCounts(inserted=1)

    params = {"scope": scope, "scope_id": scope_id, "tax_profile_id": profile_id, "treatment": treatment}
    run_write(lake, OpMeta(actor, "assign_tax", params, "assign tax assumptions"), work)


def assign_tax_profile(lake: Lake, profile_id: str | None, owner: str | None = None,
                       account: str | None = None, actor: str = "user") -> None:
    """profile_id None (accounts only) removes the override: the owner's profile applies again."""
    if profile_id is None and account is None:
        raise ValueError("a person always needs a tax profile")
    if profile_id is not None and profile_id not in load_profiles(lake.con):
        raise ValueError(f"unknown tax profile: {profile_id}")
    if (owner is None) == (account is None):
        raise ValueError("assign to exactly one of a person or an account")
    if owner is not None:
        owner_id = find_owner(lake.con, owner)
        if owner_id is None:
            raise ValueError(f"unknown person: {owner}")
        _assign(lake, "owner", owner_id, profile_id, None, actor)
    else:
        acct = find_account(lake.con, account)
        if acct is None:
            raise ValueError(f"unknown account: {account}")
        current = assignments(lake.con).get(("account", acct.account_id), (None, None))
        _assign(lake, "account", acct.account_id, profile_id, current[1], actor)


def set_account_treatment(lake: Lake, account: str, treatment: str | None, actor: str = "user") -> None:
    """Override how an account is taxed (e.g. mark it tax-exempt); None restores its type's default."""
    if treatment is not None and treatment not in TREATMENTS:
        raise ValueError(f"treatment must be one of {TREATMENTS}")
    acct = find_account(lake.con, account)
    if acct is None:
        raise ValueError(f"unknown account: {account}")
    current = assignments(lake.con).get(("account", acct.account_id), (None, None))
    _assign(lake, "account", acct.account_id, current[0], treatment, actor)


def assignments(con) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    rows = con.execute("""SELECT scope, scope_id, tax_profile_id, treatment FROM tax_assignments
        QUALIFY row_number() OVER (PARTITION BY scope, scope_id ORDER BY created_at DESC) = 1""").fetchall()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


GENERIC_LOTS = {"header_row": 0, "columns": {"symbol": "symbol", "acquired_date": "acquired_date",
                                             "shares": "shares", "cost": "cost"}}


def parse_lots(text: str, mapping: dict) -> tuple[tuple[str, date, Decimal, Decimal], ...]:
    """(symbol, acquired, shares, total cost) per lot, using a confirmed column mapping."""
    rows = read_rows(text)
    start, cols = mapping.get("header_row", 0), mapping["columns"]
    header = tuple(h.strip() for h in rows[start])
    lookup = {h.lower(): h for h in header}
    col = lambda f: lookup.get((cols.get(f) or "").lower())  # noqa: E731
    out = []
    body = rows[start + 1:]
    end = next((i for i, r in enumerate(body) if not any(c.strip() for c in r)), len(body))
    if any(looks_numeric(cell(r, header, col("shares"))) for r in body[end:]):
        raise ValueError("the lots table continues after a blank line; split the file")
    for r in body[:end]:
        symbol = clean(cell(r, header, col("symbol")))
        if symbol is None or sum(1 for c in r if c.strip()) <= 1:
            continue
        acquired, shares = parse_date(cell(r, header, col("acquired_date"))), parse_number(cell(r, header, col("shares")))
        cost = parse_number(cell(r, header, col("cost")))
        per_share = parse_number(cell(r, header, col("cost_per_share")))
        if cost is None and per_share is not None and shares is not None:
            cost = per_share * shares
        if acquired is None or shares is None or cost is None:
            raise ValueError(f"lot row for {symbol} lacks a date, quantity or cost")
        out.append((symbol, acquired, shares, cost))
    return tuple(out)


def import_lots(lake: Lake, source: Path | bytes, account: str, as_of: date, actor: str = "user",
                mapping: dict | None = None) -> str:
    """Lot details (cost = total for the lot). Default layout: symbol,acquired_date,shares,cost."""
    acct = find_account(lake.con, account)
    if acct is None:
        raise ValueError(f"unknown account: {account}")
    raw = read_source(source)
    digest = file_hash(raw)
    master = load_securities(lake.con)
    lots = []
    for symbol, acquired, shares, cost in parse_lots(raw.decode("utf-8-sig"), mapping or GENERIC_LOTS):
        sec = resolve(master, Security(None, symbol, None, "stock"))
        if sec is None:
            raise ValueError(f"unknown security in lots: {symbol} (import the statement first)")
        lots.append((acct.account_id, sec.security_id, acquired, shares, cost, as_of, digest))

    def work(con):
        if con.execute("SELECT count(*) FROM import_files WHERE file_hash = ?", [digest]).fetchone()[0]:
            raise ValueError("this file was already imported")
        n = insert_rows(con, "position_lots", tuple(lots))
        con.execute("INSERT INTO import_files VALUES (?, 'lots', NULL, ?, ?, ?, 'imported', ?)",
                    [digest, acct.account_id, as_of, utc_now(), raw])
        return None, RowCounts(inserted=n + 1)

    params = {"file_hash": digest, "account_id": acct.account_id, "as_of": as_of.isoformat()}
    return run_write(lake, OpMeta(actor, "import_lots", params, "import lot details"), work)[1]


