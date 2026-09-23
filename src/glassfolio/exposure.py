"""Look-through exposure queries (read-only)."""

from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources

from glassfolio.lake import Lake

GROUP_BYS = (None, "account", "fund")
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


def exposure_lines(lake: Lake, as_of: date) -> tuple[ExposureLine, ...]:
    rows = lake.con.execute(exposure_sql(), {"as_of": as_of}).fetchall()
    return tuple(ExposureLine(*r) for r in rows)


@dataclass(frozen=True)
class CompanyExposure:
    ticker: str | None
    name: str | None
    group: str | None  # account nickname or fund ticker, per group_by
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
    "fund": "coalesce(f.ticker, 'direct')",
}
_LATEST = """(SELECT * FROM (SELECT *, row_number() OVER (
    PARTITION BY {key} ORDER BY created_at DESC) AS rn FROM {table}) WHERE rn = 1)"""


def company_exposure(
    lake: Lake, as_of: date, ticker: str | None = None, group_by: str | None = None
) -> tuple[CompanyExposure, ...]:
    """Per-company exposure, split into held directly vs held through funds."""
    if group_by not in GROUP_BYS:
        raise ValueError(f"group_by must be one of {GROUP_BYS}")
    where = "AND upper(s.ticker) = upper($ticker)" if ticker else ""
    sql = f"""
    SELECT s.ticker, s.name, {_GROUP_EXPR[group_by]} AS grp,
        coalesce(sum(l.value) FILTER (WHERE l.via_security_id IS NULL), 0) AS direct,
        coalesce(sum(l.value) FILTER (WHERE l.via_security_id IS NOT NULL), 0) AS via,
        bool_or(l.approx), bool_or(l.value IS NULL)
    FROM ({exposure_sql()}) AS l
    JOIN {_LATEST.format(key='security_id', table='securities')} s
        ON s.security_id = l.security_id
    LEFT JOIN {_LATEST.format(key='account_id', table='accounts')} a
        ON a.account_id = l.account_id
    LEFT JOIN {_LATEST.format(key='security_id', table='securities')} f
        ON f.security_id = l.via_security_id
    WHERE l.kind = 'security' {where}
    GROUP BY ALL
    ORDER BY direct + via DESC, 1, 3
    """
    params = {"as_of": as_of, **({"ticker": ticker} if ticker else {})}
    return tuple(CompanyExposure(*r) for r in lake.con.execute(sql, params).fetchall())
