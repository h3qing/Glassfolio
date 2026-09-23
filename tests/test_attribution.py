"""Spec §4.2: exposure change = price + your money + ETF rebalancing (exactly)."""

from datetime import date

import pytest

from conftest import D, T1
from glassfolio.attribution import company_attribution
from glassfolio.exposure import Slice

TAXABLE = Slice(account="Alice Taxable")


def by_ticker(rows):
    return {r.ticker: r for r in rows}


def test_nvda_taxable_hand_computed(golden):
    lake, _ = golden
    nvda = by_ticker(company_attribution(lake, D, T1, TAXABLE))["NVDA"]
    assert nvda.start_value == pytest.approx(3300)
    assert nvda.end_value == pytest.approx(5054)
    assert nvda.price_effect == pytest.approx(324)
    assert nvda.flow_effect == pytest.approx(220)
    assert nvda.rebalance_effect == pytest.approx(1210)


def test_components_always_sum_to_change(golden):
    lake, _ = golden
    for row in company_attribution(lake, D, T1):
        explained = row.price_effect + row.flow_effect + row.rebalance_effect
        assert explained == pytest.approx(row.end_value - row.start_value, abs=1e-6), row.ticker


def test_split_between_periods_is_not_a_price_crash(golden):
    lake, _ = golden
    # Roth: 5 MSFT @ 800 on 09-12, 2:1 split on 09-16, close 400 on 09-18.
    # In post-split units that is 10 @ 400 both times: no price effect, no flow.
    msft = by_ticker(company_attribution(lake, date(2026, 9, 12), D, Slice(account="Alice Roth")))["MSFT"]
    assert msft.price_effect == pytest.approx(0)
    assert msft.flow_effect == pytest.approx(0)


def test_same_date_has_no_change(golden):
    lake, _ = golden
    for row in company_attribution(lake, D, D):
        assert row.end_value - row.start_value == pytest.approx(0)


def test_missing_price_is_flagged_not_shown_as_a_crash(golden):
    lake, _ = golden
    lake.con.execute("DELETE FROM prices WHERE date = '2026-09-30'")
    lake.con.execute("DELETE FROM prices WHERE security_id IN (SELECT security_id FROM securities "
                     "WHERE ticker = 'NVDA')")
    nvda = by_ticker(company_attribution(lake, D, T1, TAXABLE))["NVDA"]
    assert nvda.missing_price
