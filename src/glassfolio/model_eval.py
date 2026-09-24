"""Known-answer evaluation of a local model (spec §8): run before trusting one.

Uses the synthetic files in evals/ (never real data). Each file must be read
correctly — kind, header row, key columns, date, cash rows — and any
instructions planted inside a file must be ignored.
"""

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from glassfolio.llm import ChatModel, ModelError
from glassfolio.paths import resource_root
from glassfolio.understand import Reading, read_heuristic, read_with_model, validate

EVALS = resource_root() / "evals"


@dataclass(frozen=True)
class EvalResult:
    file: str
    passed: bool
    problems: tuple[str, ...]
    seconds: float


def check_reading(reading: Reading | None, expected: dict) -> tuple[str, ...]:
    if reading is None:
        return ("no reading",)
    problems = []
    if reading.kind != expected["kind"]:
        problems.append(f"kind {reading.kind} ≠ {expected['kind']}")
    if reading.header_row != expected["header_row"]:
        problems.append(f"header row {reading.header_row} ≠ {expected['header_row']}")
    problems += [f"{f}: {reading.columns.get(f)!r} ≠ {h!r}" for f, h in expected["columns"].items()
                 if reading.columns.get(f) != h]
    if "as_of" in expected and reading.as_of != date.fromisoformat(expected["as_of"]):
        problems.append(f"date {reading.as_of} ≠ {expected['as_of']}")
    problems += [f"cash row {c!r} missed" for c in expected.get("cash_symbols", []) if c not in reading.cash_symbols]
    if "fund_ticker" in expected and reading.fund_ticker != expected["fund_ticker"]:
        problems.append(f"fund ticker {reading.fund_ticker} ≠ {expected['fund_ticker']}")
    return tuple(problems)


def run_eval(model: ChatModel | None, directory: Path = EVALS) -> tuple[EvalResult, ...]:
    expected = json.loads((directory / "expected.json").read_text())
    results = []
    for name, want in sorted(expected.items()):
        raw = (directory / "files" / name).read_bytes()
        started = time.monotonic()
        try:
            if model is None:
                reading, errors = read_heuristic(raw), ()
                errors = validate(raw, reading)
            else:
                reading, errors = read_with_model(model, raw)
        except ModelError as exc:
            reading, errors = None, (str(exc),)
        problems = tuple(errors) + check_reading(reading, want)
        results.append(EvalResult(name, not problems, problems, round(time.monotonic() - started, 1)))
    return tuple(results)


def check_document(doc, expected: dict) -> tuple[str, ...]:
    """A document reading against its known answer (quantities, weights, cash, date)."""
    import csv
    import io

    from glassfolio.parsing import parse_number

    if doc.reading is None:
        return doc.errors or ("no reading",)
    r = doc.reading
    rows = list(csv.DictReader(io.StringIO(doc.table.decode())))
    problems = [] if r.kind == expected["kind"] else [f"kind {r.kind} ≠ {expected['kind']}"]
    if r.as_of is None or r.as_of.isoformat() != expected["as_of"]:
        problems.append(f"date {r.as_of} ≠ {expected['as_of']}")
    col = "Symbol" if r.kind == "positions" else "Ticker"
    by_symbol = {row[col]: row for row in rows}
    for symbol, quantity in expected.get("rows", {}).items():
        got = parse_number(by_symbol.get(symbol, {}).get("Quantity"))
        if got is None or float(got) != quantity:
            problems.append(f"{symbol} quantity {got} ≠ {quantity}")
    for symbol, weight in expected.get("weights", {}).items():
        got = parse_number(by_symbol.get(symbol, {}).get("Weight"))
        if got is None or float(got) != weight:
            problems.append(f"{symbol} weight {got} ≠ {weight}")
    if "cash" in expected:
        cash = [parse_number(row["Market Value"]) for row in rows if row[col] in r.cash_symbols]
        if [float(c) for c in cash] != [expected["cash"]]:
            problems.append(f"cash {cash} ≠ {expected['cash']}")
    if expected.get("fund_ticker") and r.fund_ticker != expected["fund_ticker"]:
        problems.append(f"fund ticker {r.fund_ticker} ≠ {expected['fund_ticker']}")
    return tuple(problems)


def run_document_eval(model: ChatModel | None, directory: Path = EVALS / "documents") -> tuple[EvalResult, ...]:
    from glassfolio.document_reader import read_document

    expected = json.loads((directory / "expected.json").read_text())
    results = []
    for name, want in sorted(expected.items()):
        started = time.monotonic()
        problems = check_document(read_document((directory / name).read_bytes(), model), want)
        results.append(EvalResult(name, not problems, problems, round(time.monotonic() - started, 1)))
    return tuple(results)


def summary(results: tuple[EvalResult, ...]) -> dict:
    return {"passed": sum(r.passed for r in results), "total": len(results),
            "seconds": round(sum(r.seconds for r in results), 1),
            "failed": [r.file for r in results if not r.passed]}
