"""Regression tests for defects found in code review (one test per finding)."""

from datetime import date
from decimal import Decimal

import pytest

from glassfolio.broker_import import commit_statement, preview_statement
from glassfolio.etf_import import commit_etf_holdings, preview_etf_holdings
from glassfolio.exposure import company_exposure, exposure_lines
from glassfolio.market_import import import_corporate_actions, import_prices
from glassfolio.recon import run_checks
from glassfolio.registry import add_account, add_owner, add_profile
from glassfolio.securities import Security, plan_securities, resolve

D = date(2026, 9, 18)
MAPPING = {"columns": {"symbol": "Symbol", "shares": "Qty", "price": "Price"},
           "cash_symbols": ["CASH"]}
HEADER = "ticker,name,asset_class,shares,weight,price,isin\n"


@pytest.fixture
def env(lake, tmp_path):
    add_owner(lake, "bob")
    add_account(lake, "T", "bob", "X", "taxable")
    profile = add_profile(lake, "X", MAPPING)

    def write(name, text):
        path = tmp_path / name
        path.write_text(text)
        return path

    def statement(body, as_of=D, name="s.csv"):
        path = write(name, "Symbol,Qty,Price\n" + body + "CASH,0,1\n")
        return preview_statement(lake, path, "T", profile, as_of)

    def etf(ticker, body, as_of, outstanding=100, name=None):
        path = write(name or f"{ticker}-{as_of}.csv", HEADER + body)
        return preview_etf_holdings(lake, path, ticker, "generic", as_of, Decimal(outstanding))

    return lake, write, statement, etf


def looked_through_equals_positions(lake, as_of=D):
    lines = exposure_lines(lake, as_of)
    top = sum(l.value for l in lines if l.kind == "position")
    looked = sum(l.value for l in lines if l.kind != "position")
    return top, looked


def test_same_fund_held_twice_in_one_account(env):
    lake, write, statement, etf = env
    commit_etf_holdings(lake, etf("FND", "AAA,A,Equity,50,0.5,10,\nBBB,B,Equity,50,0.5,10,\n",
                                  date(2026, 9, 1)))
    commit_statement(lake, statement("FND,10,10\nFND,5,10\n"))
    top, looked = looked_through_equals_positions(lake)
    assert top == pytest.approx(150) and looked == pytest.approx(150)
    (aaa,) = company_exposure(lake, D, "AAA")
    assert aaa.via_fund_value == pytest.approx(75)


def test_identical_holdings_file_under_newer_date_is_not_doubled(env):
    lake, write, statement, etf = env
    body = "AAA,A,Equity,100,1.0,10,\n"
    commit_etf_holdings(lake, etf("FND", body, date(2026, 9, 1), name="same.csv"))
    again = etf("FND", body, date(2026, 9, 2), name="same.csv")
    assert any("already imported" in e for e in again.errors)
    commit_statement(lake, statement("FND,1,10\n"))
    (aaa,) = company_exposure(lake, D, "AAA")
    assert aaa.via_fund_value == pytest.approx(10)


def test_second_holdings_version_after_type_promotion(env):
    lake, write, statement, etf = env
    commit_statement(lake, statement("FND,1,10\n"))  # FND first seen as a stock
    body = "AAA,A,Equity,50,0.5,10,\nBBB,B,Equity,50,0.5,10,\n"
    commit_etf_holdings(lake, etf("FND", body, date(2026, 9, 1)))
    v2 = etf("FND", body.replace("50,", "51,"), date(2026, 9, 2))
    assert v2.errors == ()


def test_futures_and_foreign_cash_are_valued_from_weights(env):
    lake, write, statement, etf = env
    body = ("AAA,A,Equity,80,0.8,10,\nESZ6,S&P FUTURE,Futures,1,0.1,,\n"
            "EUR,EURO,Cash,500,0.1,,\n")
    commit_etf_holdings(lake, etf("FND", body, date(2026, 9, 1)))
    commit_statement(lake, statement("FND,10,10\n"))
    report = run_checks(lake, D)
    statuses = {r.check_type: r.status for r in report.results}
    assert statuses.get("prices") is None and statuses["conservation"] == "pass"
    other = sum(l.value for l in exposure_lines(lake, D) if l.kind == "other")
    assert other == pytest.approx(20)  # 10 shares × $10 × (0.1 + 0.1)


def test_corporate_action_imported_twice_applies_once(env):
    lake, write, statement, etf = env
    commit_statement(lake, statement("MSFT,10,800\n", as_of=date(2026, 9, 12)))
    import_prices(lake, write("p.csv", "date,ticker,close\n2026-09-18,MSFT,400\n"))
    actions = write("ca.csv", "date,ticker,type,ratio_or_amount\n2026-09-16,MSFT,split,2\n")
    import_corporate_actions(lake, actions)
    import_corporate_actions(lake, actions)
    (msft,) = company_exposure(lake, D, "MSFT")
    assert msft.direct_value == pytest.approx(8000)


def test_stale_preview_cannot_commit_twice(env):
    lake, write, statement, etf = env
    preview = statement("AAA,1,10\n")
    commit_statement(lake, preview)
    with pytest.raises(ValueError, match="already imported"):
        commit_statement(lake, preview)


def test_stale_etf_preview_is_revalidated_at_commit(env):
    lake, write, statement, etf = env
    old = etf("FND", "AAA,A,Equity,100,1.0,10,\n", date(2026, 9, 1))
    commit_etf_holdings(lake, etf("FND", "AAA,A,Equity,101,1.0,10,\n", date(2026, 9, 5)))
    with pytest.raises(ValueError, match="not newer"):
        commit_etf_holdings(lake, old)


def test_same_ticker_with_and_without_isin_in_one_batch_is_one_security():
    refs = (Security(None, "AAPL", None, "stock"),
            Security(None, "AAPL", "APPLE", "stock", isin="US0378331005"))
    ids, rows = plan_securities((), refs)
    assert ids[0] == ids[1] and len(rows) == 1 and rows[0].isin == "US0378331005"
    assert resolve(rows, Security(None, "AAPL", None, "stock")) == rows[0]


def test_same_ticker_with_conflicting_isins_stays_separate():
    refs = (Security(None, "ABC", None, "stock", isin="US0000000001"),
            Security(None, "ABC", None, "stock", isin="GB0000000001"))
    ids, rows = plan_securities((), refs)
    assert ids[0] != ids[1] and len(rows) == 2


def test_fund_holding_itself_does_not_recurse(env):
    lake, write, statement, etf = env
    commit_etf_holdings(lake, etf("FND", "AAA,A,Equity,90,0.9,10,\nFND,F,Equity,10,0.1,10,\n",
                                  date(2026, 9, 1)))
    commit_statement(lake, statement("FND,10,10\n"))
    lines = exposure_lines(lake, D)
    assert not any(l.kind == "truncated" for l in lines)
    top, looked = looked_through_equals_positions(lake)
    assert looked == pytest.approx(top)
