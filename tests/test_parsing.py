import json
from decimal import Decimal

import pytest

from conftest import GOLDEN
from glassfolio.broker_import import parse_statement
from glassfolio.etf_import import parse_ishares
from glassfolio.parsing import parse_number


@pytest.mark.parametrize("raw,expected", [
    ("$1,234.50", Decimal("1234.50")), ("(12.30)", Decimal("-12.30")),
    ("-5", Decimal("-5")), ("--", None), ("", None), (None, None), ("40.00%", Decimal("40.00")),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_parse_number_rejects_garbage():
    with pytest.raises(ValueError):
        parse_number("abc")


def test_statement_skips_title_and_total_and_reads_cash():
    mapping = json.loads((GOLDEN / "broker_profile.json").read_text())
    rows = parse_statement((GOLDEN / "broker_alice_taxable.csv").read_text(), mapping)
    assert [r.symbol for r in rows] == ["NVDA", "QQQ", "GFOF", "Cash & Cash Investments"]
    cash = rows[-1]
    assert cash.is_cash and cash.shares == Decimal("1000.00") and cash.price == 1
    assert rows[1].price == Decimal("50.10") and rows[1].cost_basis == Decimal("4000.00")


def test_statement_derives_price_from_market_value():
    mapping = {"columns": {"symbol": "S", "shares": "Q", "market_value": "V"}, "cash_symbols": ["CASH"]}
    rows = parse_statement("S,Q,V\nABC,4,$10.00\nCASH,,5\n", mapping)
    assert rows[0].price == Decimal("2.5")


def test_statement_row_without_quantity_is_an_error():
    mapping = {"columns": {"symbol": "S", "shares": "Q"}}
    with pytest.raises(ValueError, match="no quantity"):
        parse_statement("S,Q\nABC,--\n", mapping)


def test_ishares_preamble_and_footer():
    h = parse_ishares((GOLDEN / "etf_gfof_ishares.csv").read_text())
    assert h.as_of.isoformat() == "2026-09-10"
    assert h.shares_outstanding == Decimal("100000.00")
    assert [r.ticker for r in h.rows] == ["QQQ", "VTI"]
    assert h.rows[0].weight == pytest.approx(0.5)
    assert h.rows[0].shares == Decimal("20000.00")


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "1E+99999"])
def test_parse_number_rejects_non_finite(raw):
    with pytest.raises(ValueError):
        parse_number(raw)


def test_printable_strips_terminal_escapes():
    from glassfolio.parsing import printable
    assert printable("NVDA\x1b[2K\x1b[1A\rAPPLE") == "NVDA[2K[1AAPPLE"


def test_non_ishares_file_is_a_clean_error():
    with pytest.raises(ValueError, match="not an iShares"):
        parse_ishares("ticker,name\nA,B\n")


def test_prices_with_incomplete_row_is_a_clean_error(lake, tmp_path):
    from glassfolio.market_import import import_prices
    path = tmp_path / "p.csv"
    path.write_text("date,ticker,close\n2026-09-18,,5\n")
    with pytest.raises(ValueError, match="incomplete row"):
        import_prices(lake, path)
