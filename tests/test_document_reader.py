"""PDF/image statements: the model transcribes, fixed code checks against the text."""

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

from glassfolio.document_reader import check_transcription, read_document

DOCS = Path(__file__).parent.parent / "evals" / "documents"

GOOD = {"kind": "positions", "as_of": "2026-09-30", "broker": "Glass Brokerage", "fund_ticker": None,
        "total_value": "$11,350.00", "rows": [
            {"symbol": "NVDA", "description": "NVIDIA CORP", "quantity": "12", "price": "$110.00",
             "market_value": "$1,320.00", "cost_basis": "$820.00", "weight": None, "is_cash": False},
            {"symbol": "QQQ", "description": "INVESCO QQQ TRUST", "quantity": "100", "price": "$52.00",
             "market_value": "$5,200.00", "cost_basis": "$4,000.00", "weight": None, "is_cash": False},
            {"symbol": "GFOF", "description": "GLASS FUND OF FUNDS", "quantity": "50", "price": "$21.00",
             "market_value": "$1,050.00", "cost_basis": "$900.00", "weight": None, "is_cash": False},
            {"symbol": "Cash", "description": "Cash & sweep balance", "quantity": None, "price": None,
             "market_value": "$3,780.00", "cost_basis": None, "weight": None, "is_cash": True}]}


class Scripted:
    name = "scripted"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, system, user, schema):
        self.prompts.append(user)
        return self.answers.pop(0)


def text_of(name):
    from glassfolio.extract import extract
    return extract((DOCS / name).read_bytes()).text


def test_good_transcription_passes():
    assert check_transcription(GOOD, text_of("statement.pdf")) == ()


def mutate(changes: dict):
    doc = copy.deepcopy(GOOD)
    for (i, field), value in changes.items():
        doc["rows"][i][field] = value
    return doc


@pytest.mark.parametrize("bad,complaint", [
    (mutate({(0, "market_value"): "$1,230.00"}), "not on NVDA's line"),      # misread digits
    (mutate({(1, "quantity"): "12"}), "not on QQQ's line"),                     # printed elsewhere, wrong row
    ({**GOOD, "rows": GOOD["rows"] + [{"symbol": "TSLA", "description": "TESLA", "quantity": "5", "price": "$1.00",
                                        "market_value": "$5.00", "cost_basis": None, "weight": None,
                                        "is_cash": False}]}, "not in the document"),  # invented row
    ({**GOOD, "rows": GOOD["rows"][:2] + GOOD["rows"][3:]}, "add up"),              # dropped row
])
def test_bad_transcriptions_are_caught(bad, complaint):
    problems = check_transcription(bad, text_of("statement.pdf"))
    assert any(complaint in p for p in problems), problems


def test_reading_a_pdf_with_a_model_retries_then_succeeds(lake):
    raw = (DOCS / "statement.pdf").read_bytes()
    model = Scripted(mutate({(0, "market_value"): "$1,230.00"}), GOOD)
    doc = read_document(raw, model)
    assert doc.errors == () and doc.reading.kind == "positions" and doc.reading.as_of == date(2026, 9, 30)
    assert "not on NVDA's line" in model.prompts[1]
    assert b"NVDA" in doc.table and doc.reading.cash_symbols == ("Cash",)


@pytest.mark.skipif(sys.platform != "darwin", reason="OCR needs macOS")
def test_screenshot_goes_through_ocr(lake):
    doc = read_document((DOCS / "statement.png").read_bytes(), Scripted(GOOD))
    assert doc.errors == () and doc.method == "ocr"


def test_without_a_model_documents_need_one(lake):
    doc = read_document((DOCS / "statement.pdf").read_bytes(), None)
    assert any("local model" in e for e in doc.errors)


def test_fund_fact_sheet(lake):
    sheet = {"kind": "fund_holdings", "as_of": "2026-06-30", "broker": None, "fund_ticker": "GCIT",
             "total_value": None, "rows": [
                 {"symbol": s, "description": n, "quantity": None, "price": None, "market_value": None,
                  "cost_basis": None, "weight": w, "is_cash": s == "USD"}
                 for s, n, w in [("NVDA", "NVIDIA Corp.", "20.00%"), ("AAPL", "Apple Inc.", "40.00%"),
                                 ("MSFT", "Microsoft Corp.", "39.00%"), ("USD", "Cash and equivalents", "1.00%")]]}
    doc = read_document((DOCS / "factsheet.pdf").read_bytes(), Scripted(sheet))
    assert doc.errors == () and doc.reading.kind == "fund_holdings" and doc.reading.fund_ticker == "GCIT"


# ---- regressions from review: line-level grounding ------------------------------------

STATEMENT = text_of("statement.pdf") if (DOCS / "statement.pdf").exists() else ""


def rows_with(changes: dict, drop=(), extra=()):
    doc = mutate(changes)
    doc["rows"] = [r for r in doc["rows"] if r["symbol"] not in drop] + list(extra)
    return doc


@pytest.mark.parametrize("bad,complaint", [
    # a number from elsewhere (the account number) as a quantity, price left out
    (rows_with({(0, "quantity"): "4471", (0, "price"): None}), "not on NVDA's line"),
    # NVDA and GFOF numbers swapped: each row still multiplies, the total still matches
    (rows_with({(0, "quantity"): "50", (0, "price"): "$21.00", (0, "market_value"): "$1,050.00",
                (2, "quantity"): "12", (2, "price"): "$110.00", (2, "market_value"): "$1,320.00"}), "not on NVDA's line"),
    # quantity and price swapped within a row (product unchanged)
    (rows_with({(1, "quantity"): "$52.00", (1, "price"): "100"}), "order"),
    # an invented ticker that is an ordinary word in the text ("Securities are not ...")
    (rows_with({}, extra=[{"symbol": "are", "description": "x", "quantity": "12", "price": "$110.00",
                           "market_value": "$1,320.00", "cost_basis": None, "weight": None, "is_cash": False}]),
     "not on are's line"),
    # a dropped row: code finds the printed total itself
    (rows_with({}, drop=("GFOF",)), "total"),
    # a holding passed off as cash
    (rows_with({(1, "is_cash"): True}), "cash"),
    # negative sign dropped/added
    (rows_with({(3, "market_value"): "-$3,780.00"}), "not on Cash's line"),
])
def test_line_level_grounding(bad, complaint):
    problems = check_transcription(bad, STATEMENT)
    assert any(complaint in p for p in problems), problems


def test_the_model_cannot_dodge_the_total_by_omitting_it():
    doc = rows_with({}, drop=("GFOF",))
    doc["total_value"] = None
    assert any("total" in p for p in check_transcription(doc, STATEMENT))


def test_date_must_be_the_statement_date_not_any_date():
    text = STATEMENT + "\nClient since 01/15/1990. Period start 09/01/2026."
    assert any("date" in p for p in check_transcription({**GOOD, "as_of": "1990-01-15"}, text))
    assert check_transcription(GOOD, text) == ()


def test_fund_ticker_must_be_printed():
    sheet = {"kind": "fund_holdings", "as_of": "2026-06-30", "broker": None, "fund_ticker": "VOO",
             "total_value": None, "rows": [{"symbol": "NVDA", "description": None, "quantity": None, "price": None,
                                            "market_value": None, "cost_basis": None, "weight": "20.00%",
                                            "is_cash": False}]}
    assert any("VOO" in p for p in check_transcription(sheet, text_of("factsheet.pdf")))


def test_hostile_pdfs_are_refused_cleanly():
    from glassfolio.extract import extract
    huge_page = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>"
                 b"endobj 3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100000 100000]>>endobj\ntrailer<</Root 1 0 R>>")
    for raw in (huge_page, b"%PDF-1.4 garbage that is not a pdf", b"%PDF-" + b"\x00" * 100):
        with pytest.raises(ValueError):
            extract(raw)


def test_malformed_rows_are_refused_not_crashing():
    doc = {**GOOD, "rows": GOOD["rows"] + ["not a row"]}
    check_transcription(doc, STATEMENT)  # must not raise
    from glassfolio.document_reader import _table
    table, reading = _table(doc)
    assert b"NVDA" in table


def test_each_row_reports_the_line_it_came_from(lake):
    model = Scripted({**GOOD, "rows": list(reversed(GOOD["rows"]))})  # rows in a different order
    doc = read_document((DOCS / "statement.pdf").read_bytes(), model)
    sources = dict(doc.sources)
    assert "NVIDIA CORP" in sources["NVDA"] and "$5,200.00" in sources["QQQ"]
