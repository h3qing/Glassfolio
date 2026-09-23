"""Exposure change attribution (spec §4.2), generalised to nested funds.

Four look-throughs, all in end-date share units (split-adjusted to t1):
    V0 = E(pos t0, baskets t0, prices t0)
    Va = E(pos t0, baskets t0, prices t1)   → price effect    = Va − V0
    Vm = E(pos t1, baskets t0, prices t1)   → your money      = Vm − Va
    V1 = E(pos t1, baskets t1, prices t1)   → fund rebalancing = V1 − Vm
The three effects sum exactly to V1 − V0. For one fund level this is the spec's
formula; weight-only funds make it approximate (their baskets move with prices).
"""

from dataclasses import dataclass
from datetime import date

from glassfolio.exposure import Dates, Slice, company_exposure
from glassfolio.lake import Lake


@dataclass(frozen=True)
class Attribution:
    ticker: str | None
    name: str | None
    start_value: float
    end_value: float
    price_effect: float
    flow_effect: float
    rebalance_effect: float
    approx: bool

    @property
    def change(self) -> float:
        return self.end_value - self.start_value


def company_attribution(
    lake: Lake, t0: date, t1: date, slice_: Slice = Slice()
) -> tuple[Attribution, ...]:
    scenarios = (Dates(t0, t0, t0, t1), Dates(t0, t0, t1, t1),
                 Dates(t1, t0, t1, t1), Dates(t1, t1, t1, t1))
    runs = tuple({(c.ticker, c.name): c for c in company_exposure(lake, d, slice_=slice_)}
                 for d in scenarios)
    keys = sorted({k for run in runs for k in run}, key=lambda k: (k[0] or "", k[1] or ""))

    def value(run: dict, key) -> float:
        row = run.get(key)
        return row.total if row else 0.0

    rows = []
    for key in keys:
        v0, va, vm, v1 = (value(run, key) for run in runs)
        approx = any(run[key].approx for run in runs if key in run)
        rows.append(Attribution(key[0], key[1], v0, v1, va - v0, vm - va, v1 - vm, approx))
    return tuple(sorted(rows, key=lambda r: -abs(r.change)))
