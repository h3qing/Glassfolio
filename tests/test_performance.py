"""Spec §2.2 / §4.4: daily snapshots, TWR and MWR."""

from datetime import date

import pytest

from conftest import D, T1
from glassfolio.exposure import Slice
from glassfolio.flows import list_inbox, resolve_flow
from glassfolio.performance import returns, xirr
from glassfolio.snapshots import snapshot_day, value_history

TAXABLE = Slice(account="Alice Taxable")


def confirm_deposit(lake):
    (item,) = list_inbox(lake)
    resolve_flow(lake, item.item_id, "deposit")


def test_twr_removes_the_deposit(golden):
    lake, _ = golden
    confirm_deposit(lake)
    r = returns(lake, D, T1, TAXABLE)
    assert r.start_value == pytest.approx(8000) and r.end_value == pytest.approx(11350)
    assert r.net_flows == pytest.approx(3000)
    assert r.twr == pytest.approx(8350 / 8000 - 1)
    assert r.open_questions == 0


def test_mwr_matches_closed_form(golden):
    lake, _ = golden
    confirm_deposit(lake)
    r = returns(lake, D, T1, TAXABLE)
    days = (T1 - D).days
    assert r.mwr == pytest.approx((8350 / 8000) ** (365 / days) - 1, rel=1e-6)


def test_open_questions_are_reported(golden):
    lake, _ = golden
    r = returns(lake, D, T1, TAXABLE)
    assert r.open_questions == 1  # the 3,000 is not yet explained


def test_transfers_inside_the_slice_cancel(golden):
    lake, _ = golden
    whole = returns(lake, D, T1)
    assert whole.start_value == pytest.approx(18000)


def test_xirr_simple_year():
    flows = ((date(2025, 1, 1), -100.0), (date(2026, 1, 1), 110.0))
    assert xirr(flows) == pytest.approx(0.10, rel=1e-6)


def test_snapshot_is_idempotent_and_sums_to_portfolio(golden):
    lake, _ = golden
    snapshot_day(lake, D)
    snapshot_day(lake, D)  # second call is a no-op
    n = lake.con.execute("SELECT count(DISTINCT computed_at) FROM portfolio_daily").fetchone()[0]
    assert n == 1
    snapshot_day(lake, T1)
    history = dict(value_history(lake))
    assert history[D] == pytest.approx(18000)
    assert history[T1] == pytest.approx(11350 + 10000 + 340)  # Roth at 09-30 closes
    assert dict(value_history(lake, TAXABLE))[T1] == pytest.approx(11350)


def test_snapshot_rebuild_supersedes(golden):
    lake, _ = golden
    snapshot_day(lake, D)
    snapshot_day(lake, D, rebuild=True)
    assert dict(value_history(lake))[D] == pytest.approx(18000)
