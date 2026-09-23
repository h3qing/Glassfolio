"""Edge cases of §4.1 beyond the golden portfolio."""

from datetime import date
from decimal import Decimal

import pytest

from conftest import GOLDEN, load_etf
from glassfolio.broker_import import commit_statement, preview_statement
from glassfolio.etf_import import commit_etf_holdings, preview_etf_holdings
from glassfolio.exposure import company_exposure, exposure_lines
from glassfolio.market_import import import_corporate_actions, import_prices
from glassfolio.registry import add_account, add_owner, add_profile

MAPPING = {"columns": {"symbol": "Symbol", "shares": "Qty", "price": "Price"},
           "cash_symbols": ["CASH"]}


def statement(lake, tmp_path, body, as_of, account="Test"):
    if not lake.con.execute("SELECT count(*) FROM owners").fetchone()[0]:
        add_owner(lake, "bob")
    add_account(lake, account, "bob", "X", "taxable")
    profile = add_profile(lake, "X", MAPPING)
    path = tmp_path / f"{account}.csv"
    path.write_text("Symbol,Qty,Price\n" + body + "CASH,0,1\n")
    return commit_statement(lake, preview_statement(lake, path, account, profile, as_of))


def test_basket_is_split_adjusted_when_holdings_predate_split(lake, tmp_path):
    load_etf(lake, "etf_qqq_2026-08-31.csv", "QQQ", as_of=date(2026, 8, 31), outstanding=Decimal(10**6))
    statement(lake, tmp_path, "QQQ,100,50\n", date(2026, 9, 1))
    prices = tmp_path / "prices.csv"
    prices.write_text("date,ticker,close\n2026-09-12,MSFT,800\n2026-09-12,QQQ,50\n")
    import_prices(lake, prices)
    import_corporate_actions(lake, GOLDEN / "corporate_actions.csv")
    (msft,) = company_exposure(lake, date(2026, 9, 16), "MSFT")
    # 100 QQQ × (45,000 × 2 / 1,000,000) = 9 shares × (800 / 2)
    assert msft.via_fund_value == pytest.approx(3600)


def test_missing_price_is_flagged_not_hidden(lake, tmp_path):
    load_etf(lake, "etf_vti.csv", "VTI", as_of=date(2026, 9, 17))
    statement(lake, tmp_path, "VTI,10,25\n", date(2026, 9, 18))
    rows = {r.ticker: r for r in company_exposure(lake, date(2026, 9, 18))}
    assert rows["NVDA"].missing_price  # weights need constituent prices


def test_fund_nesting_is_capped_at_five_levels(lake, tmp_path):
    for level in range(6, 0, -1):  # F1 holds F2 ... F6 holds NVDA
        child = "NVDA" if level == 6 else f"F{level + 1}"
        path = tmp_path / f"f{level}.csv"
        path.write_text(f"ticker,name,asset_class,shares,weight,price,isin\n{child},{child},Equity,1,1.0,1,\n")
        commit_etf_holdings(lake, preview_etf_holdings(lake, path, f"F{level}", "generic",
                                                       date(2026, 9, 1), Decimal(1)))
    statement(lake, tmp_path, "F1,1,1\n", date(2026, 9, 1))
    kinds = {l.kind for l in exposure_lines(lake, date(2026, 9, 1))}
    assert "truncated" in kinds
