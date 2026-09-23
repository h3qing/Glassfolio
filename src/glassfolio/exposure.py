"""Look-through exposure queries (read-only)."""

from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources

from glassfolio.lake import Lake

LOOK_THROUGH_KINDS = ("security", "cash", "other", "fund_residual", "truncated")


@cache
def exposure_sql() -> str:
    return resources.files("glassfolio").joinpath("sql", "exposure.sql").read_text()


@dataclass(frozen=True)
class ExposureLine:
    account_id: str
    security_id: str
    via_security_id: str | None  # None = held directly
    depth: int
    kind: str  # position | security | cash | other | fund_residual | truncated
    shares: float | None
    price: float | None
    value: float | None
    approx: bool


@dataclass(frozen=True)
class Dates:
    """The four inputs of a look-through; `Dates.at(d)` for a normal valuation."""
    pos_date: date
    basket_date: date
    price_date: date
    split_date: date

    @staticmethod
    def at(d: date) -> "Dates":
        return Dates(d, d, d, d)

    def params(self) -> dict:
        return vars(self).copy()


def _dates(as_of: "date | Dates") -> Dates:
    return as_of if isinstance(as_of, Dates) else Dates.at(as_of)


def exposure_lines(lake: Lake, as_of: "date | Dates") -> tuple[ExposureLine, ...]:
    rows = lake.con.execute(exposure_sql(), _dates(as_of).params()).fetchall()
    return tuple(ExposureLine(*r) for r in rows)


@dataclass(frozen=True)
class Slice:
    """Filter by person, account type, broker or account (nicknames, not ids)."""
    owner: str | None = None
    account_type: str | None = None
    broker: str | None = None
    account: str | None = None


_LATEST = """(SELECT * FROM (SELECT *, row_number() OVER (
    PARTITION BY {key} ORDER BY created_at DESC) AS rn FROM {table}) WHERE rn = 1)"""
_ACCOUNTS = f"""(SELECT a.account_id, a.nickname, a.account_type, a.broker,
        o.nickname AS owner
    FROM {_LATEST.format(key='account_id', table='accounts')} a
    LEFT JOIN {_LATEST.format(key='owner_id', table='owners')} o USING (owner_id))"""
_SLICE_COLUMNS = {"owner": "owner", "account_type": "account_type", "broker": "broker",
                  "account": "nickname"}


def _slice_where(slice_: Slice) -> tuple[str, dict]:
    active = {k: v for k, v in vars(slice_).items() if v is not None}
    sql = "".join(f" AND a.{_SLICE_COLUMNS[k]} = ${k}" for k in active)
    return sql, active


def accounts_in_slice(lake: Lake, slice_: Slice) -> frozenset[str]:
    where, params = _slice_where(slice_)
    rows = lake.con.execute(f"SELECT account_id FROM {_ACCOUNTS} a WHERE TRUE {where}",
                            params).fetchall()
    return frozenset(r[0] for r in rows)


@dataclass(frozen=True)
class CompanyExposure:
    ticker: str | None
    name: str | None
    group: str | None  # per group_by: account, owner, account type, broker or fund
    direct_value: float
    via_fund_value: float
    approx: bool
    missing_price: bool

    @property
    def total(self) -> float:
        return self.direct_value + self.via_fund_value


_GROUP_EXPR = {
    None: "NULL::VARCHAR",
    "account": "a.nickname",
    "owner": "a.owner",
    "account_type": "a.account_type",
    "broker": "a.broker",
    "fund": "coalesce(f.ticker, 'direct')",
}
GROUP_BYS = tuple(_GROUP_EXPR)


def company_exposure(
    lake: Lake, as_of: "date | Dates", ticker: str | None = None, group_by: str | None = None,
    slice_: Slice = Slice(),
) -> tuple[CompanyExposure, ...]:
    """Per-company exposure, split into held directly vs held through funds."""
    if group_by not in _GROUP_EXPR:
        raise ValueError(f"group_by must be one of {GROUP_BYS}")
    slice_sql, slice_params = _slice_where(slice_)
    ticker_sql = " AND upper(s.ticker) = upper($ticker)" if ticker else ""
    sql = f"""
    SELECT s.ticker, s.name, {_GROUP_EXPR[group_by]} AS grp,
        coalesce(sum(l.value) FILTER (WHERE l.via_security_id IS NULL), 0) AS direct,
        coalesce(sum(l.value) FILTER (WHERE l.via_security_id IS NOT NULL), 0) AS via,
        bool_or(l.approx), bool_or(l.value IS NULL)
    FROM ({exposure_sql()}) AS l
    JOIN {_LATEST.format(key='security_id', table='securities')} s
        ON s.security_id = l.security_id
    JOIN {_ACCOUNTS} a ON a.account_id = l.account_id
    LEFT JOIN {_LATEST.format(key='security_id', table='securities')} f
        ON f.security_id = l.via_security_id
    WHERE l.kind = 'security'{ticker_sql}{slice_sql}
    GROUP BY ALL
    ORDER BY direct + via DESC, 1, 3
    """
    params = {**_dates(as_of).params(), **slice_params, **({"ticker": ticker} if ticker else {})}
    return tuple(CompanyExposure(*r) for r in lake.con.execute(sql, params).fetchall())


@dataclass(frozen=True)
class PortfolioSummary:
    total_value: float       # Σ top-level positions at close
    cash_value: float        # USD cash, direct and inside funds
    other_value: float       # futures, foreign cash, fund residuals
    approx_value: float      # look-through value resting on weights or proxies
    missing_prices: int      # securities without a price (values incomplete)


def portfolio_summary(lake: Lake, as_of: date, slice_: Slice = Slice()) -> PortfolioSummary:
    accounts = accounts_in_slice(lake, slice_)
    lines = tuple(l for l in exposure_lines(lake, as_of) if l.account_id in accounts)
    total = lambda kinds: sum(l.value or 0 for l in lines if l.kind in kinds)  # noqa: E731
    return PortfolioSummary(
        total_value=total(("position",)),
        cash_value=total(("cash",)),
        other_value=total(("other", "fund_residual", "truncated")),
        approx_value=sum(l.value or 0 for l in lines
                         if l.approx and l.kind in LOOK_THROUGH_KINDS),
        missing_prices=len({l.security_id for l in lines if l.value is None}),
    )
