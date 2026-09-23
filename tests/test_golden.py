"""End-to-end on the synthetic golden portfolio. Expected values are hand-computed
(see tests/golden/README.md); they must match exactly."""

from datetime import date

import pytest

from conftest import D
from glassfolio.exposure import company_exposure, exposure_lines
from glassfolio.recon import run_checks
from glassfolio.registry import find_account


def by_ticker(rows):
    return {r.ticker: r for r in rows}


def test_nvda_total_split_direct_and_via_funds(golden):
    lake, _ = golden
    (nvda,) = company_exposure(lake, D, ticker="NVDA")
    # direct 10×100; QQQ 100×0.2×100; GFOF→QQQ 200; GFOF→VTI 100; VTI 1000; GCIT→VTI 200
    assert nvda.direct_value == pytest.approx(1000)
    assert nvda.via_fund_value == pytest.approx(3500)
    assert nvda.total == pytest.approx(4500)
    assert nvda.approx  # VTI is weight-only, GCIT is proxied


def test_all_companies(golden):
    lake, _ = golden
    rows = by_ticker(company_exposure(lake, D))
    assert rows["AAPL"].total == pytest.approx(3700)
    assert rows["MSFT"].total == pytest.approx(8580)
    assert rows["MSFT"].direct_value == pytest.approx(4000)  # 5 sh pre-split → 10 × 400
    assert set(rows) == {"NVDA", "AAPL", "MSFT"}


def test_group_by_account_and_fund(golden):
    lake, _ = golden
    per_account = {r.group: r.total for r in company_exposure(lake, D, "NVDA", "account")}
    assert per_account == pytest.approx({"Alice Taxable": 3300, "Alice Roth": 1200})
    per_fund = {r.group: r.total for r in company_exposure(lake, D, "NVDA", "fund")}
    assert per_fund == pytest.approx({"direct": 1000, "QQQ": 2000, "GFOF": 300,
                                      "VTI": 1000, "GCIT": 200})


def test_lines_conserve_portfolio_value(golden):
    lake, _ = golden
    lines = exposure_lines(lake, D)
    top = sum(l.value for l in lines if l.kind == "position")
    looked = sum(l.value for l in lines if l.kind != "position")
    assert top == pytest.approx(18000)
    assert looked == pytest.approx(18000)
    cash = sum(l.value for l in lines if l.kind == "cash")
    assert cash == pytest.approx(1220)  # 1000 direct + QQQ 200 + GFOF→QQQ 20


def test_uses_close_not_export_price_for_analysis(golden):
    lake, _ = golden
    (qqq_id,) = lake.con.execute("SELECT DISTINCT security_id FROM securities "
                                 "WHERE ticker = 'QQQ'").fetchone()
    (qqq,) = [l for l in exposure_lines(lake, D) if l.kind == "position" and l.security_id == qqq_id]
    assert qqq.price == pytest.approx(50.0)  # export said 50.10


def test_holdings_version_follows_as_of_date(golden):
    lake, _ = golden
    later = company_exposure(lake, date(2026, 9, 30), "NVDA", "fund")
    qqq = {r.group: r for r in later}["QQQ"]
    assert qqq.total == pytest.approx(100 * 0.3 * 999)  # 09-25 basket, 09-25 price


def test_account_total_reconciles_with_export_prices(golden):
    lake, _ = golden
    taxable = find_account(lake.con, "Alice Taxable").account_id
    report = run_checks(lake, D, taxable, reported_total=8010, reported_cost=5500)
    statuses = {r.check_type: r.status for r in report.results}
    assert statuses["account_total"] == statuses["cost_total"] == "pass"
    assert report.status == "warn"  # GFOF holds weight-only VTI
    roth = find_account(lake.con, "Alice Roth").account_id
    report = run_checks(lake, date(2026, 9, 12), roth, reported_total=9880)
    assert {r.check_type: r.status for r in report.results}["account_total"] == "pass"


def test_account_total_mismatch_explains_likely_cause(golden):
    lake, _ = golden
    taxable = find_account(lake.con, "Alice Taxable").account_id
    report = run_checks(lake, D, taxable, reported_total=9010)  # off by the GFOF position
    total = {r.check_type: r for r in report.results}["account_total"]
    assert total.status == "fail"
    assert "GFOF" in total.hint


def test_portfolio_checks_warn_on_approximations(golden):
    lake, _ = golden
    report = run_checks(lake, D)
    statuses = {r.check_type: r.status for r in report.results}
    assert statuses["conservation"] == "pass"
    assert statuses["approximation"] == "warn"
    assert report.status == "warn"
