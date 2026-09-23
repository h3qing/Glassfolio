"""Broker position statements: fixed parsing engine + per-broker mapping config.

Two steps: `preview_statement` parses and matches securities without writing;
`commit_statement` writes only after the user has confirmed the preview.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from glassfolio.lake import Lake, OpMeta, RowCounts, run_write, utc_now
from glassfolio.parsing import cell, clean, file_hash, looks_numeric, parse_number, read_rows, read_source
from glassfolio.registry import find_account, load_profile
from glassfolio.securities import Security, ensure_securities, load_securities, resolve

CASH = Security(None, "USD", "US DOLLAR", "cash")
REQUIRED_COLUMNS = ("symbol", "shares")


@dataclass(frozen=True)
class StatementRow:
    symbol: str
    description: str | None
    shares: Decimal
    price: Decimal
    cost_basis: Decimal | None
    is_cash: bool

    @property
    def market_value(self) -> Decimal:
        return self.shares * self.price


@dataclass(frozen=True)
class RowMatch:
    row: StatementRow
    security_id: str | None
    master_name: str | None
    status: str  # matched | new | cash


@dataclass(frozen=True)
class StatementPreview:
    file_hash: str
    account_id: str
    profile_id: str | None
    as_of: date
    matches: tuple[RowMatch, ...]
    duplicate: bool
    raw: bytes

    @property
    def total_value(self) -> Decimal:
        return sum((m.row.market_value for m in self.matches), Decimal(0))


def _parse_row(row, header, mapping) -> StatementRow | None:
    cols = mapping["columns"]
    symbol = clean(cell(row, header, cols["symbol"]))
    if symbol is None or symbol in mapping.get("skip_symbols", ()):
        return None
    if sum(1 for c in row if c.strip()) <= 1 and symbol not in mapping.get("cash_symbols", ()):
        return None  # a footer or note line, not a holding
    get = lambda key: parse_number(cell(row, header, cols.get(key)))  # noqa: E731
    cost = get("cost_basis")
    if cost is None and get("cost_per_share") is not None and get("shares") is not None:
        cost = get("cost_per_share") * get("shares")
    if symbol in mapping.get("cash_symbols", ()):
        amount = get("market_value") if get("market_value") is not None else get("shares")
        return StatementRow(symbol, None, amount or Decimal(0), Decimal(1), None, True)
    shares = get("shares")
    if shares is None:
        raise ValueError(f"row for {symbol} has no quantity")
    price = get("price")
    if price is None:
        value = get("market_value")
        if value is None or shares == 0:
            raise ValueError(f"row for {symbol} has neither price nor market value")
        price = value / shares
    desc = clean(cell(row, header, cols.get("description")))
    return StatementRow(symbol, desc, shares, price, cost, False)


def parse_statement(text: str, mapping: dict) -> tuple[StatementRow, ...]:
    missing = [c for c in REQUIRED_COLUMNS if c not in mapping.get("columns", {})]
    if missing:
        raise ValueError(f"mapping lacks columns: {missing}")
    rows = read_rows(text)
    header_row = mapping.get("header_row", 0)
    if not 0 <= header_row < len(rows):
        raise ValueError("the header row is outside the file")
    header = tuple(h.strip() for h in rows[header_row])
    body = rows[header_row + 1:]
    end = next((i for i, r in enumerate(body) if not any(c.strip() for c in r)), len(body))
    check_nothing_after_blank(body[end:], header, mapping["columns"].get("shares"))
    parsed = (_parse_row(r, header, mapping) for r in body[:end])  # a blank line ends the table
    return tuple(p for p in parsed if p is not None)


def check_nothing_after_blank(rest, header, numeric_column: str | None) -> None:
    """A blank line ends a table; refuse files where holdings continue after it."""
    after = [r for r in rest if looks_numeric(cell(r, header, numeric_column))]
    if after:
        raise ValueError(f"the table continues after a blank line ({len(after)} more rows); "
                         "files with several sections aren't supported yet, so split the file")


def _match(master, row: StatementRow) -> RowMatch:
    if row.is_cash:
        return RowMatch(row, None, "US DOLLAR", "cash")
    sec = resolve(master, Security(None, row.symbol, row.description, "stock"))
    if sec is None:
        return RowMatch(row, None, None, "new")
    return RowMatch(row, sec.security_id, sec.name, "matched")


def _already_imported(con, digest: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM import_files WHERE file_hash = ? AND status = 'imported'", [digest]
    ).fetchone()[0] > 0


def preview_statement(
    lake: Lake, source: Path | bytes, account: str, profile_id: str | None, as_of: date,
    mapping: dict | None = None,
) -> StatementPreview:
    """`mapping` (e.g. a reading the user is confirming) takes the place of a saved profile."""
    acct = find_account(lake.con, account)
    if acct is None:
        raise ValueError(f"unknown account: {account}")
    raw = read_source(source)
    digest = file_hash(raw)
    if mapping is None and profile_id is None:
        raise ValueError("choose a saved layout or confirm a reading of the file")
    rows = parse_statement(raw.decode("utf-8-sig"), mapping or load_profile(lake.con, profile_id))
    if not any(r.is_cash for r in rows):
        raise ValueError("statement has no cash row; buys and sells would look like deposits")
    master = load_securities(lake.con)
    duplicate = _already_imported(lake.con, digest)
    matches = tuple(_match(master, r) for r in rows)
    return StatementPreview(digest, acct.account_id, profile_id, as_of, matches, duplicate, raw)


def _ref(m: RowMatch) -> Security:
    if m.row.is_cash:
        return CASH
    return Security(None, m.row.symbol, m.row.description, "stock")


def commit_statement(lake: Lake, preview: StatementPreview, actor: str = "user") -> str:
    """Write a previewed statement. Call only after the user confirmed it."""
    if preview.duplicate:
        raise ValueError("this file was already imported")

    def work(con):
        if _already_imported(con, preview.file_hash):  # re-check: previews can go stale
            raise ValueError("this file was already imported")
        ids, new_secs = ensure_securities(con, tuple(_ref(m) for m in preview.matches))
        for sec_id, m in zip(ids, preview.matches):
            r = m.row
            con.execute(
                "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [preview.account_id, sec_id, r.shares, r.cost_basis, r.price,
                 preview.as_of, preview.file_hash],
            )
            if not r.is_cash:
                con.execute(
                    "INSERT INTO prices VALUES (?, ?, ?, NULL, 'broker_export')",
                    [sec_id, preview.as_of, r.price],
                )
        con.execute(
            "INSERT INTO import_files VALUES (?, 'statement', ?, ?, ?, ?, 'imported', ?)",
            [preview.file_hash, preview.profile_id, preview.account_id, preview.as_of,
             utc_now(), preview.raw],
        )
        n = len(preview.matches)
        n_prices = sum(1 for m in preview.matches if not m.row.is_cash)
        return None, RowCounts(inserted=new_secs + n + n_prices + 1)

    params = {"file_hash": preview.file_hash, "account_id": preview.account_id,
              "profile_id": preview.profile_id, "as_of": preview.as_of.isoformat()}
    return run_write(lake, OpMeta(actor, "import_statement", params,
                                  "import broker position statement"), work)[1]
