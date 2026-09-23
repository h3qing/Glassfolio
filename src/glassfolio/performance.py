"""Returns (spec §4.4): time-weighted and money-weighted.

Statement dates are the period boundaries, and flows are assumed to happen on
them, so both figures are approximate; more frequent statements make them more
accurate. Transfers between two accounts that are both in the slice cancel out.
"""

from dataclasses import dataclass
from datetime import date

from glassfolio.exposure import Slice, accounts_in_slice, portfolio_summary
from glassfolio.flows import list_cash_flows, list_inbox
from glassfolio.lake import Lake

EXTERNAL = ("deposit", "withdrawal", "transfer")


@dataclass(frozen=True)
class Returns:
    start: date
    end: date
    start_value: float
    end_value: float
    net_flows: float
    twr: float | None         # cumulative over the period
    mwr: float | None         # annualised (XIRR)
    open_questions: int       # unexplained flows not yet answered: returns may be off


def xirr(flows: tuple[tuple[date, float], ...]) -> float | None:
    """Annual rate r with Σ amount / (1 + r)^(days/365) = 0, by bisection."""
    if not flows or all(a >= 0 for _, a in flows) or all(a <= 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(rate: float) -> float:
        return sum(a / (1 + rate) ** ((d - t0).days / 365) for d, a in flows)

    lo, hi = -0.9999, 1e6
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _statement_dates(lake: Lake, accounts: frozenset[str], start: date, end: date) -> tuple[date, ...]:
    rows = lake.con.execute(
        """SELECT DISTINCT as_of_date FROM import_files WHERE kind = 'statement'
           AND status = 'imported' AND as_of_date > ? AND as_of_date < ?
           AND list_contains(?, account_id) ORDER BY 1""", [start, end, list(accounts)]).fetchall()
    return tuple(r[0] for r in rows)


def returns(lake: Lake, start: date, end: date, slice_: Slice = Slice()) -> Returns:
    accounts = accounts_in_slice(lake, slice_)
    flows = [f for f in list_cash_flows(lake)
             if f.account_id in accounts and f.type in EXTERNAL and start < f.date <= end]
    by_date = {d: sum(f.amount for f in flows if f.date == d) for d in {f.date for f in flows}}
    points = sorted({start, end, *by_date, *_statement_dates(lake, accounts, start, end)})
    values = [portfolio_summary(lake, d, slice_).total_value for d in points]

    growth = 1.0
    for prev, value, d in zip(values, values[1:], points[1:]):
        if prev > 0:
            growth *= (value - by_date.get(d, 0.0)) / prev
    mwr_flows = ((points[0], -values[0]), *((d, -a) for d, a in sorted(by_date.items())),
                 (points[-1], values[-1]))
    questions = sum(1 for i in list_inbox(lake) if i.type == "unexplained_flow"
                    and i.payload["account_id"] in accounts
                    and start < date.fromisoformat(i.payload["end"]) <= end)
    return Returns(start, end, values[0], values[-1], sum(by_date.values()),
                   growth - 1 if values[0] > 0 else None, xirr(mwr_flows), questions)
