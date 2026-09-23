"""Daily closes and corporate actions from normalized CSV files.

Tickers must already exist in the security master; unknown ones are rejected
rather than guessed.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from glassfolio.lake import Lake, OpMeta, RowCounts, insert_rows, run_write
from glassfolio.parsing import cell, clean, parse_number, printable, read_rows, read_source
from glassfolio.securities import Security, load_securities, resolve

CA_TYPES = ("split", "dividend")


@dataclass(frozen=True)
class MarketRow:
    day: date
    ticker: str
    kind: str
    value: Decimal


def _read(source: Path | bytes, value_col: str, kind_col: str | None) -> tuple[MarketRow, ...]:
    rows = read_rows(read_source(source).decode("utf-8-sig"))
    header = tuple(h.strip().lower() for h in rows[0])
    missing = [c for c in ("date", "ticker", value_col, kind_col) if c and c not in header]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return tuple(_market_row(r, header, value_col, kind_col)
                 for r in rows[1:] if any(c.strip() for c in r))


def _market_row(r, header, value_col: str, kind_col: str | None) -> MarketRow:
    day, ticker, value = (clean(cell(r, header, c)) for c in ("date", "ticker", value_col))
    if day is None or ticker is None or value is None:
        raise ValueError(f"incomplete row: {printable(','.join(r))}")
    kind = (clean(cell(r, header, kind_col)) or "").lower() if kind_col else "close"
    return MarketRow(date.fromisoformat(day), ticker.upper(), kind, parse_number(value))


def _resolve_all(lake: Lake, rows: tuple[MarketRow, ...]) -> dict[str, str]:
    master = load_securities(lake.con)
    found = {t: resolve(master, Security(None, t, None, "other")) for t in {r.ticker for r in rows}}
    missing = sorted(t for t, s in found.items() if s is None)
    if missing:
        raise ValueError(f"unknown tickers (import holdings or statements first): {missing}")
    return {t: s.security_id for t, s in found.items()}


def import_prices(lake: Lake, path: Path | bytes, source: str = "manual", actor: str = "user") -> str:
    """CSV columns: date,ticker,close."""
    rows = _read(path, "close", None)
    ids = _resolve_all(lake, rows)

    def work(con):
        n = insert_rows(con, "prices", tuple(
            (ids[r.ticker], r.day, r.value, None, source) for r in rows))
        return None, RowCounts(inserted=n)

    params = {"source": source, "tickers": len(ids)}
    return run_write(lake, OpMeta(actor, "import_prices", params, "import closes"), work)[1]


def import_corporate_actions(lake: Lake, path: Path | bytes, actor: str = "user") -> str:
    """CSV columns: date,ticker,type,ratio_or_amount. A 2:1 split has ratio 2."""
    rows = _read(path, "ratio_or_amount", "type")
    bad = sorted({r.kind for r in rows} - set(CA_TYPES))
    if bad:
        raise ValueError(f"unknown corporate action types: {bad}")
    ids = _resolve_all(lake, rows)

    def work(con):
        existing = set(con.execute(
            "SELECT security_id, date, type FROM corporate_actions").fetchall())
        new = tuple(dict.fromkeys(
            (ids[r.ticker], r.day, r.kind, r.value) for r in rows
            if (ids[r.ticker], r.day, r.kind) not in existing))
        return None, RowCounts(inserted=insert_rows(con, "corporate_actions", new))

    return run_write(lake, OpMeta(actor, "import_corporate_actions", {"rows": len(rows)},
                                  "import corporate actions"), work)[1]
