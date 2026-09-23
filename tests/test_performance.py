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
    assert r.mwr_period == pytest.approx(8350 / 8000 - 1, rel=1e-6)


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


def test_account_opened_mid_period_is_money_added_not_gain(lake, tmp_path):
    import json
    from conftest import GOLDEN
    from glassfolio.broker_import import commit_statement, preview_statement
    from glassfolio.registry import add_account, add_owner, add_profile
    add_owner(lake, "bob")
    add_account(lake, "A", "bob", "G", "taxable")
    add_account(lake, "B", "bob", "G", "taxable")
    profile = add_profile(lake, "G", json.loads((GOLDEN / "broker_profile.json").read_text()))
    for acct, day, cash in (("A", date(2026, 1, 31), 10000), ("A", date(2026, 3, 31), 10000),
                            ("B", date(2026, 2, 28), 50000)):
        path = tmp_path / f"{acct}{day}.csv"
        path.write_text(f'"{acct} as of {day}"\n"Symbol","Description","Quantity","Price","Market Value",'
                        f'"Cost Basis"\n"Cash & Cash Investments","--","--","--","${cash}","--"\n')
        commit_statement(lake, preview_statement(lake, path, acct, profile, day))
    r = returns(lake, date(2026, 1, 31), date(2026, 3, 31))
    assert r.net_flows == pytest.approx(50000)
    assert r.twr == pytest.approx(0) and r.mwr == pytest.approx(0, abs=1e-6)


def test_missing_prices_are_reported(golden):
    lake, _ = golden
    lake.con.execute("DELETE FROM prices")
    r = returns(lake, D, T1)
    assert r.missing_prices > 0
