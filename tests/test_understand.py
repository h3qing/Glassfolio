"""Reading arbitrary exports: saved profile → local model → heuristics, always validated."""

import json
from datetime import date
from pathlib import Path

import pytest

from glassfolio.understand import read_file, read_heuristic, validate

EVALS = Path(__file__).parent.parent / "evals"
EXPECTED = json.loads((EVALS / "expected.json").read_text())


def check(reading, expected):
    from glassfolio.model_eval import check_reading
    assert check_reading(reading, expected) == ()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_heuristics_read_every_eval_file(name):
    raw = (EVALS / "files" / name).read_bytes()
    reading = read_heuristic(raw)
    check(reading, EXPECTED[name])
    assert validate(raw, reading) == ()


class ScriptedModel:
    """Returns canned answers in order; records prompts."""
    name = "scripted"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, system, user, schema):
        self.prompts.append(user)
        return self.answers.pop(0)


GOOD = {"kind": "positions", "header_row": 1, "as_of": "2026-09-18", "broker": "Generic",
        "columns": {"symbol": "Symbol", "description": "Description", "shares": "Quantity", "price": "Price",
                    "market_value": "Market Value", "cost_basis": "Cost Basis"},
        "cash_symbols": ["Cash & Cash Investments"], "skip_symbols": ["Account Total"],
        "fund_ticker": None, "shares_outstanding": None, "weight_is_percent": None}


def test_model_reading_is_used_and_validated(lake):
    raw = (EVALS / "files" / "injection_positions.csv").read_bytes()
    reading, errors = read_file(lake, raw, ScriptedModel(GOOD))
    assert errors == () and reading.source == "model" and reading.broker == "Generic"


def test_injected_or_wrong_answer_is_caught_and_retried(lake):
    raw = (EVALS / "files" / "injection_positions.csv").read_bytes()
    hijacked = {**GOOD, "columns": {**GOOD["columns"], "shares": "Price"},
                "cash_symbols": ["NVDA", "Cash & Cash Investments"]}
    model = ScriptedModel(hijacked, GOOD)
    reading, errors = read_file(lake, raw, model)
    assert errors == () and reading.columns["shares"] == "Quantity"
    assert "NVDA" not in reading.cash_symbols
    assert "doesn't match" in model.prompts[1]  # the validator's complaint went back to the model


def test_invented_columns_are_rejected(lake):
    raw = (EVALS / "files" / "plain_positions.csv").read_bytes()
    invented = {**GOOD, "header_row": 0, "columns": {"symbol": "Ticker", "shares": "Units Held"},
                "cash_symbols": ["CASH"], "skip_symbols": []}
    reading, errors = read_file(lake, raw, ScriptedModel(invented, invented))
    assert reading.source == "heuristic"  # model failed twice → fall back
    assert reading.columns["shares"] == "Shares" and errors == ()


def test_no_model_uses_heuristics(lake):
    raw = (EVALS / "files" / "fidelity_style_positions.csv").read_bytes()
    reading, errors = read_file(lake, raw, None)
    assert reading.source == "heuristic" and errors == ()


def test_confirmed_mapping_is_recognized_next_time_without_a_model(lake):
    from glassfolio.understand import remember_reading
    raw = (EVALS / "files" / "etrade_style_positions.csv").read_bytes()
    reading, _ = read_file(lake, raw, None)
    profile_id = remember_reading(lake, reading, raw, broker="E*TRADE")
    later = raw.replace(b"09/30/2026", b"10/31/2026").replace(b"AAPL,205.00", b"AAPL,206.00")

    class Boom:
        name = "boom"

        def complete_json(self, *a):
            raise AssertionError("model must not be called for a known format")

    again, errors = read_file(lake, later, Boom())
    assert again.source == "saved" and again.profile_id == profile_id and again.broker == "E*TRADE"
    assert again.as_of == date(2026, 10, 31) and errors == ()


def test_eval_runner_scores_heuristics_and_a_model():
    from glassfolio.model_eval import run_eval, summary
    assert summary(run_eval(None))["passed"] == len(EXPECTED)

    class Wrong:
        name = "wrong"

        def complete_json(self, *a):
            return {**GOOD, "header_row": 0}

    scored = summary(run_eval(Wrong()))
    assert scored["passed"] < scored["total"]


def test_settings_round_trip_and_refuse_remote(tmp_path, monkeypatch):
    from glassfolio.llm import ModelError
    from glassfolio.settings import choose_model, configured_model, load_settings
    monkeypatch.setenv("GLASSFOLIO_HOME", str(tmp_path))
    assert configured_model() is None
    choose_model("http://127.0.0.1:1234/v1", "some-model")
    assert load_settings().name == "some-model" and configured_model().base_url == "http://127.0.0.1:1234/v1"
    with pytest.raises(ModelError):
        choose_model("http://example.com/v1", "x")


def test_model_field_mixups_and_off_by_one_header_are_repaired(lake):
    raw = (EVALS / "files" / "lots_style.csv").read_bytes()
    sloppy = {**GOOD, "kind": "lots", "header_row": 0, "as_of": "2026-09-18",
              "columns": {"symbol": "Symbol", "acquired_date": "Open Date", "shares": "Quantity",
                          "cost_basis": "Cost Basis"}, "cash_symbols": [], "skip_symbols": []}
    reading, errors = read_file(lake, raw, ScriptedModel(sloppy))
    assert errors == () and reading.source == "model"
    assert reading.header_row == 1 and reading.columns["cost"] == "Cost Basis"


# ---- regressions from review: a wrong answer must never validate ------------------

INJ = EVALS / "files" / "injection_positions.csv"


def answer(**changes):
    return {**GOOD, **changes, "columns": {**GOOD["columns"], **changes.get("columns", {})}}


@pytest.mark.parametrize("bad", [
    answer(columns={"shares": "Price", "price": "Quantity"}),            # swapped
    answer(columns={"market_value": None, "shares": "Cost Basis"}),      # dodges the value check
    answer(columns={"price": None}, cash_symbols=["Cash & Cash Investments", "NVDA"]),  # holding as cash
    answer(skip_symbols=["Account Total", "NVDA"]),                       # drops a real holding
])
def test_wrong_readings_do_not_validate(bad):
    from glassfolio.understand import Reading, _from_model, _normalize
    raw = INJ.read_bytes()
    reading, problems = _normalize(raw, _from_model(bad))
    assert problems + validate(raw, reading), bad


@pytest.mark.parametrize("junk", [[], {"kind": 3, "columns": {"symbol": 5}}, {"columns": "x"}])
def test_malformed_model_replies_fall_back(lake, junk):
    reading, errors = read_file(lake, INJ.read_bytes(), ScriptedModel(junk, junk))
    assert reading.source == "heuristic" and errors == ()


def test_rows_after_a_blank_line_are_an_error_not_silently_dropped():
    raw = b'Symbol,Quantity,Price,Value\nAAA,1,10,10\nCASH,,,5\n\nBBB,2,20,40\n'
    reading = read_heuristic(raw)
    assert any("blank line" in e for e in validate(raw, reading))


def test_saved_layout_is_rechecked(lake):
    from glassfolio.understand import remember_reading
    raw = INJ.read_bytes()
    reading, _ = read_file(lake, raw, None)
    from dataclasses import replace
    remember_reading(lake, replace(reading, skip_symbols=("Account Total", "NVDA")), raw, "X")
    again, errors = read_file(lake, raw, None)
    assert "NVDA" not in again.skip_symbols
