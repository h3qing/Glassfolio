"""After-tax values (spec §2.3). Planning assumptions, not tax advice.

    taxable   V − gain × rate; lots split long/short term (held > 1 year),
              else the profile's assumption (default: all short-term)
    deferred  V × (1 − withdrawal rate)          (traditional IRA, 401k)
    exempt    V                                  (Roth IRA, HSA, 529, or overridden)

Look-through values are apportioned: every line held via a position carries that
position's after-tax/pre-tax factor, so fund-held companies bear the fund's tax.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from itertools import groupby

from glassfolio.exposure import Slice, accounts_in_slice, exposure_lines
from glassfolio.lake import Lake
from glassfolio.tax_profiles import (DEFAULT_PROFILE, TaxProfile, assignments, default_treatment,
                                     effective_rates, load_profiles)

LOT_TOLERANCE = 0.005  # lots must cover the position's shares within 0.5%


@dataclass(frozen=True)
class Scenario:
    """What-if overrides applied on top of every profile, never saved."""
    federal_ltcg_rate: float | None = None
    federal_ordinary_rate: float | None = None
    state_rate: float | None = None
    withdrawal_rate: float | None = None
    niit: bool | None = None
    no_lot_assumption: str | None = None
    prices: dict = field(default_factory=dict)  # ticker → price

    def apply(self, p: TaxProfile) -> TaxProfile:
        changes = {k: v for k, v in vars(self).items() if k != "prices" and v is not None}
        return replace(p, **changes)


@dataclass(frozen=True)
class PositionTax:
    account_id: str
    account: str
    security_id: str
    ticker: str | None
    treatment: str
    value: float
    cost: float | None
    lt_gain: float
    st_gain: float
    tax: float
    basis: str        # lots | assumed | assumed (lots incomplete) | no cost | n/a
    profile: str

    @property
    def after_tax(self) -> float:
        return self.value - self.tax

    @property
    def missing_cost(self) -> bool:
        return self.basis == "no cost"


def _accounts(con) -> dict[str, tuple[str, str, str]]:
    rows = con.execute("""SELECT account_id, nickname, owner_id, account_type FROM accounts
        QUALIFY row_number() OVER (PARTITION BY account_id ORDER BY created_at DESC) = 1""").fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def _costs_and_lots(con, as_of: date):
    costs = dict(((r[0], r[1]), r[2]) for r in con.execute("""
        WITH li AS (SELECT account_id, file_hash FROM import_files
            WHERE kind = 'statement' AND status = 'imported' AND as_of_date <= ?
            QUALIFY row_number() OVER (PARTITION BY account_id ORDER BY as_of_date DESC, imported_at DESC) = 1)
        SELECT p.account_id, p.security_id,
               CASE WHEN count(p.cost_basis) = count(*) THEN sum(p.cost_basis)::DOUBLE END
        FROM positions p JOIN li ON li.account_id = p.account_id AND li.file_hash = p.import_file_hash
        GROUP BY ALL""", [as_of]).fetchall())
    # Lot shares are split-adjusted like positions. A lot file older than the account's
    # statement no longer describes the position, so it is marked stale.
    lot_rows = con.execute("""
        WITH li AS (SELECT account_id, file_hash, as_of_date FROM import_files
            WHERE kind = 'lots' AND status = 'imported' AND as_of_date <= $d
            QUALIFY row_number() OVER (PARTITION BY account_id ORDER BY as_of_date DESC, imported_at DESC) = 1),
        st AS (SELECT account_id, max(as_of_date) AS stmt_date FROM import_files
            WHERE kind = 'statement' AND status = 'imported' AND as_of_date <= $d GROUP BY account_id)
        SELECT l.account_id, l.security_id, l.acquired_date,
            l.shares::DOUBLE * coalesce((SELECT product(DISTINCT ca.ratio_or_amount::DOUBLE) FROM corporate_actions ca
                WHERE ca.security_id = l.security_id AND ca.type = 'split'
                  AND ca.date > l.as_of_date AND ca.date <= $d), 1),
            l.cost::DOUBLE, li.as_of_date < coalesce(st.stmt_date, li.as_of_date)
        FROM position_lots l JOIN li ON li.account_id = l.account_id AND li.file_hash = l.import_file_hash
        LEFT JOIN st ON st.account_id = l.account_id
        ORDER BY 1, 2""", {"d": as_of}).fetchall()
    lots = {k: (tuple((r[2], r[3], r[4]) for r in rows), rows[0][5])
            for k, g in groupby(lot_rows, key=lambda r: (r[0], r[1])) for rows in [tuple(g)]}
    return costs, lots


def is_long_term(acquired: date, valued: date) -> bool:
    """Held more than one year: sold after the one-year anniversary of the purchase."""
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:  # 29 Feb → the anniversary falls on 1 Mar
        anniversary = date(acquired.year + 1, 3, 1)
    return valued > anniversary


def _profile_for(account_id: str, owner_id: str, profiles, assigned) -> TaxProfile:
    pid = (assigned.get(("account", account_id), (None, None))[0]
           or assigned.get(("owner", owner_id), (None, None))[0])
    return profiles.get(pid, DEFAULT_PROFILE)


def _taxable_gains(value, price, shares, cost, lots, as_of, assumption):
    """(lt_gain, st_gain, basis)."""
    usable, stale = lots if lots else ((), False)
    if usable and not stale and abs(sum(s for _, s, _ in usable) - shares) <= LOT_TOLERANCE * max(abs(shares), 1e-9):
        lt = sum(s * price - c for acquired, s, c in usable if is_long_term(acquired, as_of))
        st = sum(s * price - c for acquired, s, c in usable if not is_long_term(acquired, as_of))
        return lt, st, "lots"
    if cost is None:
        return 0.0, 0.0, "no cost"
    gain = value - cost
    basis = ("assumed (lots out of date)" if stale else "assumed (lots incomplete)") if usable else "assumed"
    return (gain, 0.0, basis) if assumption == "long_term" else (0.0, gain, basis)


def treatment_of(account_id: str, account_type: str, assigned) -> str:
    return assigned.get(("account", account_id), (None, None))[1] or default_treatment(account_type)


def position_taxes(lake: Lake, as_of: date, scenario: Scenario = Scenario()) -> tuple[PositionTax, ...]:
    con = lake.con
    accounts, profiles, assigned = _accounts(con), load_profiles(con), assignments(con)
    costs, lots = _costs_and_lots(con, as_of)
    secs = dict(con.execute("""SELECT security_id, ticker || '|' || type FROM securities
        QUALIFY row_number() OVER (PARTITION BY security_id ORDER BY created_at DESC) = 1""").fetchall())
    out = []
    for line in (l for l in exposure_lines(lake, as_of) if l.kind == "position"):
        nickname, owner_id, account_type = accounts[line.account_id]
        ticker, sec_type = (secs.get(line.security_id) or "|").split("|", 1)
        profile = scenario.apply(_profile_for(line.account_id, owner_id, profiles, assigned))
        treatment = treatment_of(line.account_id, account_type, assigned)
        price = scenario.prices.get(ticker, line.price)
        value = (line.shares or 0) * price if price is not None else 0.0
        if price is None:  # can't value it, so can't tax it; exposure flags the missing price
            out.append(PositionTax(line.account_id, nickname, line.security_id, ticker or None, treatment_of(
                line.account_id, account_type, assigned), 0.0, None, 0.0, 0.0, 0.0, "no price", ""))
            continue
        rates = effective_rates(profile)
        cost = costs.get((line.account_id, line.security_id))
        lt = st = 0.0
        basis = "n/a"
        if treatment == "deferred":
            tax = value * rates.withdrawal
        elif treatment == "exempt" or sec_type == "cash":
            tax = 0.0
        else:
            lt, st, basis = _taxable_gains(value, price or 0, line.shares or 0, cost,
                                           lots.get((line.account_id, line.security_id)), as_of,
                                           profile.no_lot_assumption)
            tax = lt * rates.ltcg + st * rates.stcg
            if not profile.count_losses:
                tax = max(tax, 0.0)
        out.append(PositionTax(line.account_id, nickname, line.security_id, ticker or None, treatment,
                               value, cost, lt, st, tax, basis, profile.name))
    return tuple(out)


@dataclass(frozen=True)
class AfterTax:
    pre_tax: float
    tax: float
    missing_cost: int
    by_treatment: dict

    @property
    def after_tax(self) -> float:
        return self.pre_tax - self.tax


def portfolio_after_tax(lake: Lake, as_of: date, slice_: Slice = Slice(),
                        scenario: Scenario = Scenario()) -> AfterTax:
    accounts = accounts_in_slice(lake, slice_)
    rows = [p for p in position_taxes(lake, as_of, scenario) if p.account_id in accounts]
    by_treatment = {t: {"pre_tax": sum(p.value for p in rows if p.treatment == t),
                        "tax": sum(p.tax for p in rows if p.treatment == t)}
                    for t in sorted({p.treatment for p in rows})}
    return AfterTax(sum(p.value for p in rows), sum(p.tax for p in rows),
                    sum(1 for p in rows if p.missing_cost), by_treatment)


@dataclass(frozen=True)
class CompanyAfterTax:
    ticker: str | None
    name: str | None
    total: float
    after_tax: float
    direct_after_tax: float
    via_fund_after_tax: float


def company_after_tax(lake: Lake, as_of: date, slice_: Slice = Slice(),
                      scenario: Scenario = Scenario()) -> tuple[CompanyAfterTax, ...]:
    """Per-company look-through value after tax (each line carries its position's factor)."""
    accounts = accounts_in_slice(lake, slice_)
    factor = {(p.account_id, p.security_id): (p.after_tax / p.value if p.value else 1.0)
              for p in position_taxes(lake, as_of, scenario)}
    names = {r[0]: (r[1], r[2]) for r in lake.con.execute("""SELECT security_id, ticker, name FROM securities
        QUALIFY row_number() OVER (PARTITION BY security_id ORDER BY created_at DESC) = 1""").fetchall()}
    lines = sorted(((names.get(l.security_id, (None, None)), l.value,
                     l.value * factor.get((l.account_id, l.via_security_id or l.security_id), 1.0),
                     l.via_security_id is None)
                    for l in exposure_lines(lake, as_of)
                    if l.kind == "security" and l.account_id in accounts and l.value is not None),
                   key=lambda t: (t[0][0] or "", t[0][1] or ""))
    rows = []
    for (ticker, name), group in groupby(lines, key=lambda t: t[0]):
        g = tuple(group)
        rows.append(CompanyAfterTax(ticker, name, sum(t[1] for t in g), sum(t[2] for t in g),
                                    sum(t[2] for t in g if t[3]), sum(t[2] for t in g if not t[3])))
    return tuple(sorted(rows, key=lambda r: -r.total))
