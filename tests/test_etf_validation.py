from datetime import date
from decimal import Decimal

import pytest

from conftest import GOLDEN, load_etf
from glassfolio.etf_import import (HoldingRow, HoldingsFile, PreviousVersion,
                                   commit_etf_holdings, preview_etf_holdings, validate)


def row(weight, ticker="X", asset="Equity"):
    return HoldingRow(ticker, "name", asset, Decimal(1), weight, None)


def holdings(*rows, as_of=date(2026, 9, 17)):
    return HoldingsFile(as_of, Decimal(100), tuple(rows))


def test_valid_file_passes():
    assert validate(holdings(row(0.6), row(0.4)), PreviousVersion(date(2026, 9, 1), 2)) == ()


def test_weights_out_of_range():
    (err,) = validate(holdings(row(0.5), row(0.4)), None)
    assert "95%-105%" in err


def test_unmapped_equity_weight():
    errors = validate(holdings(row(0.9), row(0.1, ticker=None)), None)
    assert any("maps to securities" in e for e in errors)


def test_non_equity_rows_are_kept_as_other():
    assert validate(holdings(row(0.9), row(0.1, ticker="ESZ6", asset="Futures")), None) == ()


def test_not_newer_than_previous():
    errors = validate(holdings(row(1.0)), PreviousVersion(date(2026, 9, 17), 1))
    assert any("not newer" in e for e in errors)


def test_holding_count_jump():
    errors = validate(holdings(row(0.5), row(0.5)), PreviousVersion(date(2026, 9, 1), 10))
    assert any("count changed" in e for e in errors)


def test_rejected_file_is_not_published_and_previous_version_stays(lake):
    load_etf(lake, "etf_qqq_2026-09-17.csv", "QQQ", as_of=date(2026, 9, 17), outstanding=Decimal(10**6))
    stale = preview_etf_holdings(lake, GOLDEN / "etf_qqq_2026-08-31.csv", "QQQ", "generic",
                                 date(2026, 8, 31), Decimal(10**6))
    assert stale.errors
    with pytest.raises(ValueError, match="failed validation"):
        commit_etf_holdings(lake, stale)
    dates = lake.con.execute("SELECT DISTINCT as_of_date FROM etf_holdings").fetchall()
    assert dates == [(date(2026, 9, 17),)]
