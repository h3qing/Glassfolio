"""Assisted import: drop any export, the local model reads it, you confirm.

Flow: /api/assist/read (upload) → a proposed reading the UI lets you correct →
/api/assist/preview (the usual preview, security names included) → the existing
/api/import/commit, which also remembers the confirmed layout for next time.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from glassfolio import broker_import, etf_import
from glassfolio.lake import Lake, new_id
from glassfolio.llm import list_models
from glassfolio.model_eval import run_eval, summary
from glassfolio.parsing import clean, parse_number, read_rows
from glassfolio.server.serialize import to_json
from glassfolio.settings import choose_model, configured_model, load_settings, record_eval, set_touch_id
from glassfolio.tax_profiles import parse_lots
from glassfolio.understand import FIELDS, KINDS, Reading, read_file, validate
from glassfolio.server.api import keep_recent

SAMPLE_ROWS = 6


@dataclass(frozen=True)
class Upload:
    raw: bytes
    reading: Reading


def _reading_json(reading: Reading, raw: bytes, errors) -> dict:
    rows = read_rows(raw.decode("utf-8-sig", errors="replace"))
    sample = [list(r) for r in rows[reading.header_row + 1: reading.header_row + 1 + SAMPLE_ROWS]]
    return {**to_json(reading), "fields": FIELDS, "errors": list(errors), "sample": sample,
            "lines": [" ".join(r)[:160] for r in rows[:reading.header_row]]}


def _edited(base: Reading, body: dict, raw: bytes) -> Reading:
    """Apply the user's corrections to a proposed reading."""
    kind = body.get("kind", base.kind)
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    header_row = int(body.get("header_row", base.header_row))
    if header_row != base.header_row:
        rows = read_rows(raw.decode("utf-8-sig", errors="replace"))
        if not 0 <= header_row < len(rows):
            raise ValueError("the header row is outside the file")
        base = replace(base, header=tuple(c.strip() for c in rows[header_row]))
    columns = {f: (body.get("columns") or base.columns).get(f) or None for f in FIELDS[kind]}
    outstanding = body.get("shares_outstanding", base.shares_outstanding)
    as_of = body.get("as_of")
    return replace(
        base, kind=kind, header_row=header_row, columns=columns,
        as_of=date.fromisoformat(as_of) if as_of else base.as_of,
        cash_symbols=tuple(body.get("cash_symbols", base.cash_symbols)),
        skip_symbols=tuple(body.get("skip_symbols", base.skip_symbols)),
        fund_ticker=(clean(body.get("fund_ticker")) or base.fund_ticker or "").upper() or None,
        shares_outstanding=parse_number(str(outstanding)) if outstanding not in (None, "") else None,
        weight_is_percent=bool(body.get("weight_is_percent", base.weight_is_percent)),
        broker=clean(body.get("broker")) or base.broker,
        source="user" if body.get("edited") else base.source)


def check_lots(lake: Lake, raw: bytes, account: str | None, lots) -> None:
    """Fail at preview, not at commit: account, securities and duplicates."""
    from glassfolio.parsing import file_hash
    from glassfolio.registry import find_account
    from glassfolio.securities import Security, load_securities, resolve

    if not account or find_account(lake.con, account) is None:
        raise ValueError("choose the account these lots belong to")
    master = load_securities(lake.con)
    unknown = sorted({s for s, *_ in lots if resolve(master, Security(None, s, None, "stock")) is None})
    if unknown:
        raise ValueError(f"unknown securities {unknown}: import the positions statement first")
    if lake.con.execute("SELECT count(*) FROM import_files WHERE file_hash = ?", [file_hash(raw)]).fetchone()[0]:
        raise ValueError("this file was already imported")


class AssistApi:
    def __init__(self, lake: Lake, api):
        self.lake = lake
        self.api = api  # the main Api: shares the pending-preview store and commit
        self.uploads: dict[str, Upload] = {}

    async def models(self, request: Request) -> JSONResponse:
        s = load_settings()
        available = list_models(s.url)
        return JSONResponse(to_json({"url": s.url, "name": s.name, "available": available,
                                     "reachable": bool(available), "evals": s.evals,
                                     "require_touch_id": s.require_touch_id}))

    async def touch_id(self, request: Request) -> JSONResponse:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("required"), bool):
            raise ValueError("required must be true or false")
        return JSONResponse({"require_touch_id": set_touch_id(body["required"]).require_touch_id})

    async def choose(self, request: Request) -> JSONResponse:
        b = await request.json()
        s = choose_model(b.get("url") or load_settings().url, b.get("name"))
        return JSONResponse(to_json({"url": s.url, "name": s.name}))

    async def evaluate(self, request: Request) -> JSONResponse:
        model = configured_model()
        results = await run_in_threadpool(run_eval, model)  # slow: keep the server responsive
        result = summary(results)
        if model is not None:
            record_eval(model.name, result)
        return JSONResponse(to_json({"model": model.name if model else None, **result,
                                     "results": results}))

    async def read(self, request: Request) -> JSONResponse:
        from glassfolio.server.api import _upload
        _, raw = await _upload(request)
        reading, errors = await run_in_threadpool(read_file, self.lake, raw, configured_model())
        token = new_id("up")
        self.uploads = keep_recent({**self.uploads, token: Upload(raw, reading)})
        return JSONResponse({"token": token, **_reading_json(reading, raw, errors)})

    async def preview(self, request: Request) -> JSONResponse:
        body = await request.json()
        upload = self.uploads.get(body.get("token", ""))
        if upload is None:
            raise ValueError("upload expired; drop the file again")
        reading = _edited(upload.reading, body.get("reading") or {}, upload.raw)
        if reading.as_of is None:
            raise ValueError("no date was found in the file; enter the as-of date")
        errors = validate(upload.raw, reading)
        if errors:
            return JSONResponse({"error": "; ".join(errors), "errors": list(errors),
                                 "reading": to_json(reading)}, status_code=422)
        remember = (reading, upload.raw, body.get("broker") or reading.broker) \
            if reading.source != "saved" else None
        if reading.kind == "positions":
            p = broker_import.preview_statement(self.lake, upload.raw, body["account"], reading.profile_id,
                                                reading.as_of, mapping=reading.mapping())
            payload = self.api.statement_payload(p, reading.skip_symbols)
            return self.api._hold("statement", p, payload, remember)
        if reading.kind == "lots":
            lots = parse_lots(upload.raw.decode("utf-8-sig"), reading.mapping())
            check_lots(self.lake, upload.raw, body.get("account"), lots)
            pending = (upload.raw, body["account"], reading.as_of, reading.mapping())
            return self.api._hold("lots", pending, {
                "kind": "lots", "count": len(lots), "as_of": reading.as_of, "errors": [],
                "lots": [{"symbol": s, "acquired": a, "shares": q, "cost": c} for s, a, q, c in lots[:50]]},
                remember)
        if not reading.fund_ticker:
            raise ValueError("enter the fund's ticker")
        p = etf_import.preview_etf_holdings(self.lake, upload.raw, reading.fund_ticker, "mapped",
                                            reading.as_of, reading.shares_outstanding, reading.mapping())
        return self.api._hold("etf", p, self.api.etf_payload(p), remember)
