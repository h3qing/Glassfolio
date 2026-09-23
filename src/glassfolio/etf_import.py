"""ETF / fund holdings: parse, validate, then publish a new version.

Fetching may fail; publishing must not be wrong. A file that fails any
validation gate is rejected and the previous version stays in force.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from glassfolio.lake import Lake, OpMeta, RowCounts, insert_rows, run_write, utc_now
from glassfolio.parsing import cell, clean, file_hash, looks_numeric, parse_number, read_rows, read_source
from glassfolio.securities import Security, ensure_securities, load_securities, resolve

WEIGHT_RANGE = (0.95, 1.05)
MAX_COUNT_CHANGE = 0.20
MIN_MAPPED_WEIGHT = 0.97
FORMATS = ("generic", "ishares", "mapped")


@dataclass(frozen=True)
class HoldingRow:
    ticker: str | None
    name: str | None
    asset_class: str
    shares: Decimal | None
    weight: float | None  # fraction of fund, 0..1
    price: Decimal | None
    isin: str | None = None
    cusip: str | None = None

    @property
    def security_type(self) -> str:
        kind = self.asset_class.lower()
        if kind in ("equity", "stock"):
            return "stock"
        is_usd = (self.ticker or self.name or "").upper().startswith("USD")
        # Foreign cash is carried as `other` (valued by weight), never as $1/unit.
        return "cash" if kind in ("cash", "money market") and is_usd else "other"

    @property
    def mapped(self) -> bool:
        """Equity rows need an identifier; cash and other rows map by label."""
        if self.security_type != "stock":
            return bool(self.ticker or self.name)
        return bool(self.ticker or self.isin or self.cusip)


@dataclass(frozen=True)
class HoldingsFile:
    as_of: date
    shares_outstanding: Decimal | None
    rows: tuple[HoldingRow, ...]


def parse_generic(text: str, as_of: date, shares_outstanding: Decimal | None) -> HoldingsFile:
    """Columns: ticker,name,asset_class,shares,weight(fraction),price,isin[,cusip]."""
    rows = read_rows(text)
    header = tuple(h.strip().lower() for h in rows[0])
    get = lambda r, c: cell(r, header, c)  # noqa: E731
    parsed = tuple(
        HoldingRow(
            ticker=clean(get(r, "ticker")), name=clean(get(r, "name")),
            asset_class=clean(get(r, "asset_class")) or "Equity",
            shares=parse_number(get(r, "shares")),
            weight=_float(parse_number(get(r, "weight"))),
            price=parse_number(get(r, "price")),
            isin=clean(get(r, "isin")), cusip=clean(get(r, "cusip")),
        )
        for r in rows[1:] if any(c.strip() for c in r)
    )
    return HoldingsFile(as_of, shares_outstanding, parsed)


_CASHLIKE = ("cash", "money market", "usd", "dollar")


def parse_mapped(text: str, mapping: dict, as_of: date, shares_outstanding: Decimal | None) -> HoldingsFile:
    """Any issuer's layout, described by a mapping the user confirmed (often proposed by the model)."""
    rows = read_rows(text)
    start = mapping.get("header_row", 0)
    header = tuple(h.strip() for h in rows[start])
    cols = mapping.get("columns", {})
    scale = 100 if mapping.get("weight_is_percent", True) else 1
    get = lambda r, f: cell(r, header, cols.get(f))  # noqa: E731
    out = []
    body = rows[start + 1:]
    end = next((i for i, r in enumerate(body) if not any(c.strip() for c in r)), len(body))
    numeric = cols.get("weight") or cols.get("shares")
    if any(looks_numeric(get(r, "weight") or get(r, "shares")) for r in body[end:]):
        raise ValueError(f"the holdings table continues after a blank line (column {numeric}); split the file")
    for r in body[:end]:
        ticker, name = clean(get(r, "ticker")), clean(get(r, "name"))
        if ticker is None and name is None:
            continue
        label = f"{ticker or ''} {name or ''}".lower()
        asset = clean(get(r, "asset_class")) or ("Cash" if any(w in label for w in _CASHLIKE) else "Equity")
        weight = parse_number(get(r, "weight"))
        out.append(HoldingRow(ticker, name, asset, parse_number(get(r, "shares")),
                              None if weight is None else float(weight) / scale, parse_number(get(r, "price")),
                              isin=clean(get(r, "isin")), cusip=clean(get(r, "cusip"))))
    return HoldingsFile(as_of, shares_outstanding, tuple(out))


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def parse_ishares(text: str) -> HoldingsFile:
    """iShares holdings CSV: preamble (as-of date, shares outstanding), table, footer."""
    rows = read_rows(text)
    meta = {r[0].strip(): r[1] for r in rows if len(r) >= 2 and r[0].strip()}
    if "Fund Holdings as of" not in meta:
        raise ValueError("not an iShares holdings file: no 'Fund Holdings as of' line")
    as_of = datetime.strptime(meta["Fund Holdings as of"].strip(), "%b %d, %Y").date()
    outstanding = parse_number(meta.get("Shares Outstanding"))
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Ticker"), None)
    if start is None:
        raise ValueError("not an iShares holdings file: no 'Ticker' header row")
    header = tuple(h.strip() for h in rows[start])
    table = []
    for r in rows[start + 1:]:
        if len(r) < len(header):
            break
        cell = dict(zip(header, r))
        weight = parse_number(cell.get("Weight (%)"))
        table.append(HoldingRow(
            ticker=clean(cell.get("Ticker")), name=clean(cell.get("Name")),
            asset_class=clean(cell.get("Asset Class")) or "Other",
            shares=parse_number(cell.get("Quantity")),
            weight=None if weight is None else float(weight) / 100,
            price=parse_number(cell.get("Price")),
        ))
    return HoldingsFile(as_of, outstanding, tuple(table))


@dataclass(frozen=True)
class PreviousVersion:
    as_of: date
    count: int


def validate(new: HoldingsFile, previous: PreviousVersion | None) -> tuple[str, ...]:
    """Return the list of failed gates; empty means safe to publish."""
    errors: list[str] = []
    if not new.rows:
        return ("file has no holdings",)
    total_weight = sum(r.weight or 0 for r in new.rows)
    if not WEIGHT_RANGE[0] <= total_weight <= WEIGHT_RANGE[1]:
        errors.append(f"weights sum to {total_weight:.2%}, expected 95%-105%")
    mapped = sum(r.weight or 0 for r in new.rows if r.mapped)
    if total_weight and mapped / total_weight < MIN_MAPPED_WEIGHT:
        errors.append(f"only {mapped / total_weight:.1%} of weight maps to securities")
    if previous is not None:
        if new.as_of <= previous.as_of:
            errors.append(f"as-of {new.as_of} is not newer than {previous.as_of}")
        change = abs(len(new.rows) - previous.count) / previous.count
        if change > MAX_COUNT_CHANGE:
            errors.append(f"holding count changed by {change:.0%} (limit 20%)")
    return tuple(errors)


@dataclass(frozen=True)
class EtfPreview:
    etf_ticker: str
    source: str
    file_hash: str
    holdings: HoldingsFile
    errors: tuple[str, ...]


def _previous(con, etf_ticker: str) -> PreviousVersion | None:
    etf = resolve(load_securities(con), Security(None, etf_ticker, None, "etf"))
    if etf is None:
        return None
    row = con.execute(
        """SELECT as_of_date, count(*) FROM etf_holdings WHERE etf_id = ?
           GROUP BY as_of_date, fetched_at ORDER BY as_of_date DESC, fetched_at DESC LIMIT 1""",
        [etf.security_id],
    ).fetchone()
    return PreviousVersion(row[0], row[1]) if row else None


def _already_imported(con, digest: str) -> bool:
    return con.execute("SELECT count(*) FROM etf_holdings WHERE raw_file_hash = ?",
                       [digest]).fetchone()[0] > 0


def _gate_errors(con, etf_ticker: str, holdings: HoldingsFile, digest: str) -> tuple[str, ...]:
    duplicate = ("this file was already imported",) if _already_imported(con, digest) else ()
    return duplicate + validate(holdings, _previous(con, etf_ticker))


def preview_etf_holdings(
    lake: Lake, source: Path | bytes, etf_ticker: str, fmt: str,
    as_of: date | None = None, shares_outstanding: Decimal | None = None, mapping: dict | None = None,
) -> EtfPreview:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    raw = read_source(source)
    text = raw.decode("utf-8-sig")
    if fmt == "ishares":
        holdings = parse_ishares(text)
    elif fmt == "mapped":
        if as_of is None or mapping is None:
            raise ValueError("a mapped holdings file needs its mapping and as-of date")
        holdings = parse_mapped(text, mapping, as_of, shares_outstanding)
    else:
        if as_of is None:
            raise ValueError("generic format needs an explicit as-of date")
        holdings = parse_generic(text, as_of, shares_outstanding)
    digest = file_hash(raw)
    errors = _gate_errors(lake.con, etf_ticker, holdings, digest)
    return EtfPreview(etf_ticker.upper(), f"file:{fmt}", digest, holdings, errors)


def _holding_ref(r: HoldingRow) -> Security:
    ticker = r.ticker if r.security_type == "stock" else (r.ticker or r.name)
    return Security(None, ticker, r.name, r.security_type, cusip=r.cusip, isin=r.isin)


def commit_etf_holdings(lake: Lake, preview: EtfPreview, actor: str = "user") -> str:
    if preview.errors:
        raise ValueError("holdings failed validation: " + "; ".join(preview.errors))
    h = preview.holdings
    fetched = utc_now()

    def work(con):
        errors = _gate_errors(con, preview.etf_ticker, h, preview.file_hash)
        if errors:  # the lake may have changed since the preview
            raise ValueError("holdings failed validation: " + "; ".join(errors))
        (etf_id,), n_etf = ensure_securities(
            con, (Security(None, preview.etf_ticker, None, "etf"),), force_type="etf")
        ids, n_secs = ensure_securities(con, tuple(_holding_ref(r) for r in h.rows))
        pairs = tuple(zip(ids, h.rows))
        insert_rows(con, "etf_holdings", tuple(
            (etf_id, sec_id, h.as_of, r.shares, r.weight, h.shares_outstanding,
             preview.source, preview.file_hash, fetched) for sec_id, r in pairs))
        n_prices = insert_rows(con, "prices", tuple(
            (sec_id, h.as_of, r.price, None, "etf_file") for sec_id, r in pairs
            if r.price is not None and r.security_type == "stock"))
        return None, RowCounts(inserted=n_etf + n_secs + len(h.rows) + n_prices)

    params = {"etf": preview.etf_ticker, "file_hash": preview.file_hash,
              "as_of": h.as_of.isoformat(), "source": preview.source}
    return run_write(lake, OpMeta(actor, "import_etf_holdings", params,
                                  "publish new ETF holdings version"), work)[1]
