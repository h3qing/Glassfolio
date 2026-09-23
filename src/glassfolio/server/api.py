"""JSON API handlers. Reads are free; writes go preview → commit."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from starlette.requests import Request
from starlette.responses import JSONResponse

from glassfolio import broker_import, etf_import, market_import, recon, registry
from glassfolio.attribution import company_attribution
from glassfolio.exposure import Slice, company_exposure, portfolio_summary
from glassfolio.flows import list_inbox, resolve_flow
from glassfolio.performance import returns
from glassfolio.snapshots import value_history
from glassfolio.tax import company_after_tax, portfolio_after_tax
from glassfolio.tax_profiles import import_lots
from glassfolio.understand import remember_reading
from glassfolio.lake import Lake, list_ops, new_id
from glassfolio.server.serialize import to_json

MAX_UPLOAD = 20 * 1024 * 1024
MAX_HELD = 20  # previews/uploads kept in memory; older ones expire


def keep_recent(store: dict) -> dict:
    """Newest MAX_HELD entries (dicts keep insertion order)."""
    return dict(list(store.items())[-MAX_HELD:])


@dataclass(frozen=True)
class Pending:
    kind: str  # statement | etf | lots
    preview: object
    remember: tuple | None = None  # (reading, raw, broker): save the confirmed layout on commit


class Api:
    """Holds the lake and previews awaiting confirmation (in memory only)."""

    def __init__(self, lake: Lake):
        self.lake = lake
        self.pending: dict[str, Pending] = {}

    # ---- reads -------------------------------------------------------------
    def _as_of(self, request: Request) -> date:
        raw = request.query_params.get("as_of")
        return date.fromisoformat(raw) if raw else self.default_as_of()

    def default_as_of(self) -> date:
        row = self.lake.con.execute(
            "SELECT max(as_of_date) FROM import_files WHERE kind = 'statement'").fetchone()
        return row[0] or date.today()

    @staticmethod
    def _slice(request: Request) -> Slice:
        q = request.query_params
        return Slice(q.get("owner") or None, q.get("account_type") or None,
                     q.get("broker") or None, q.get("account") or None)

    async def meta(self, request: Request) -> JSONResponse:
        con = self.lake.con
        dates = con.execute("SELECT DISTINCT as_of_date FROM import_files "
                            "WHERE kind = 'statement' ORDER BY 1 DESC").fetchall()
        accounts = registry.list_accounts(con)
        return JSONResponse(to_json({
            "owners": registry.list_owners(con),
            "accounts": accounts,
            "account_types": registry.ACCOUNT_TYPES,
            "brokers": sorted({a.broker for a in accounts}),
            "profiles": [{"profile_id": p, "broker": b} for p, b in registry.list_profiles(con)],
            "statement_dates": [d[0] for d in dates],
            "default_as_of": self.default_as_of(),
        }))

    async def exposure(self, request: Request) -> JSONResponse:
        as_of, slice_ = self._as_of(request), self._slice(request)
        summary = portfolio_summary(self.lake, as_of, slice_)
        taxed = {(c.ticker, c.name): c for c in company_after_tax(self.lake, as_of, slice_)}
        companies = [{**to_json(c), **({"after_tax": t.after_tax, "direct_after_tax": t.direct_after_tax,
                                        "via_fund_after_tax": t.via_fund_after_tax}
                                       if (t := taxed.get((c.ticker, c.name))) else {})}
                     for c in company_exposure(self.lake, as_of, slice_=slice_)]
        after = portfolio_after_tax(self.lake, as_of, slice_)
        return JSONResponse(to_json({"as_of": as_of, "companies": companies, "summary": {
            **to_json(summary), "after_tax": after.after_tax, "tax": after.tax,
            "missing_cost": after.missing_cost}}))

    async def company(self, request: Request) -> JSONResponse:
        as_of, slice_ = self._as_of(request), self._slice(request)
        ticker = request.path_params["ticker"]
        by = {g: company_exposure(self.lake, as_of, ticker, g, slice_)
              for g in ("fund", "account", "owner")}
        return JSONResponse(to_json({"as_of": as_of, "ticker": ticker, **by}))

    async def checks(self, request: Request) -> JSONResponse:
        rows = self.lake.con.execute("""
            SELECT r.scope, a.nickname, r.check_type, r.as_of_date, r.expected, r.actual,
                   r.diff, r.status, r.hint, r.run_at
            FROM recon_results r
            LEFT JOIN (SELECT DISTINCT account_id, nickname FROM accounts) a
              ON r.scope = 'account:' || a.account_id
            QUALIFY r.run_at = max(r.run_at) OVER (PARTITION BY r.scope)
            ORDER BY r.run_at DESC""").fetchall()
        keys = ("scope", "account", "check_type", "as_of", "expected", "actual", "diff",
                "status", "hint", "run_at")
        return JSONResponse(to_json([dict(zip(keys, r)) for r in rows]))

    async def changes(self, request: Request) -> JSONResponse:
        q, slice_ = request.query_params, self._slice(request)
        start, end = date.fromisoformat(q["start"]), date.fromisoformat(q["end"])
        if start > end:
            raise ValueError("the start date must be before the end date")
        return JSONResponse(to_json({
            "start": start, "end": end,
            "returns": returns(self.lake, start, end, slice_),
            "after_tax": {"start": portfolio_after_tax(self.lake, start, slice_).after_tax,
                          "end": portfolio_after_tax(self.lake, end, slice_).after_tax},
            "companies": [{**to_json(a), "change": a.change}
                          for a in company_attribution(self.lake, start, end, slice_)]}))

    async def history(self, request: Request) -> JSONResponse:
        rows = value_history(self.lake, self._slice(request))
        return JSONResponse(to_json([{"date": d, "value": v} for d, v in rows]))

    async def inbox(self, request: Request) -> JSONResponse:
        return JSONResponse(to_json(list_inbox(self.lake)))

    async def answer(self, request: Request) -> JSONResponse:
        b = await request.json()
        op = resolve_flow(self.lake, b["item_id"], b["classification"], b.get("pair") or None,
                          bool(b.get("remember")))
        return JSONResponse({"op_id": op})

    async def ops(self, request: Request) -> JSONResponse:
        return JSONResponse(to_json(list_ops(self.lake, 100)))

    # ---- writes ------------------------------------------------------------
    async def run_checks(self, request: Request) -> JSONResponse:
        body = await request.json()
        account_id = None
        if body.get("account"):
            acct = registry.find_account(self.lake.con, body["account"])
            if acct is None:
                raise ValueError(f"unknown account: {body['account']}")
            account_id = acct.account_id
        report = recon.run_checks(
            self.lake, date.fromisoformat(body["as_of"]), account_id,
            _opt_float(body.get("reported_total")), _opt_float(body.get("reported_cost")))
        return JSONResponse(to_json(report))

    async def add_owner(self, request: Request) -> JSONResponse:
        body = await request.json()
        return JSONResponse({"owner_id": registry.add_owner(self.lake, body["nickname"])})

    async def add_account(self, request: Request) -> JSONResponse:
        b = await request.json()
        account_id = registry.add_account(self.lake, b["nickname"], b["owner"], b["broker"],
                                          b["account_type"])
        return JSONResponse({"account_id": account_id})

    async def add_profile(self, request: Request) -> JSONResponse:
        b = await request.json()
        mapping = json.loads(b["mapping"]) if isinstance(b["mapping"], str) else b["mapping"]
        return JSONResponse({"profile_id": registry.add_profile(self.lake, b["broker"], mapping)})

    @staticmethod
    def statement_payload(p, skipped: tuple = ()) -> dict:
        return {"kind": "positions",
                "rows": [{"symbol": m.row.symbol, "description": m.row.description,
                          "master_name": m.master_name, "status": m.status,
                          "shares": m.row.shares, "price": m.row.price,
                          "market_value": m.row.market_value} for m in p.matches],
                "total_value": p.total_value, "duplicate": p.duplicate, "as_of": p.as_of,
                "skipped": list(skipped),
                "errors": ["this file was already imported"] if p.duplicate else []}

    @staticmethod
    def etf_payload(p) -> dict:
        h = p.holdings
        return {"kind": "fund_holdings", "etf": p.etf_ticker, "as_of": h.as_of, "count": len(h.rows),
                "weight_sum": sum(r.weight or 0 for r in h.rows),
                "shares_outstanding": h.shares_outstanding, "errors": list(p.errors),
                "top": [{"ticker": r.ticker, "name": r.name, "weight": r.weight}
                        for r in sorted(h.rows, key=lambda r: -(r.weight or 0))[:10]]}

    async def preview_statement(self, request: Request) -> JSONResponse:
        form, raw = await _upload(request)
        p = broker_import.preview_statement(self.lake, raw, form["account"], form["profile_id"],
                                            date.fromisoformat(form["as_of"]))
        return self._hold("statement", p, self.statement_payload(p))

    async def preview_etf(self, request: Request) -> JSONResponse:
        form, raw = await _upload(request)
        as_of = date.fromisoformat(form["as_of"]) if form.get("as_of") else None
        outstanding = Decimal(form["shares_outstanding"]) if form.get("shares_outstanding") else None
        p = etf_import.preview_etf_holdings(self.lake, raw, form["etf"], form.get("format") or
                                            "generic", as_of, outstanding)
        return self._hold("etf", p, self.etf_payload(p))

    def _hold(self, kind: str, preview, payload: dict, remember: tuple | None = None) -> JSONResponse:
        token = new_id("pv")
        self.pending = keep_recent({**self.pending, token: Pending(kind, preview, remember)})
        return JSONResponse(to_json({"token": token, **payload}))

    def _commit_one(self, pending: Pending) -> str:
        if pending.kind == "statement":
            op = broker_import.commit_statement(self.lake, pending.preview)
        elif pending.kind == "lots":
            raw, account, as_of, mapping = pending.preview
            op = import_lots(self.lake, raw, account, as_of, mapping=mapping)
        else:
            op = etf_import.commit_etf_holdings(self.lake, pending.preview)
        if pending.remember:  # only once the import has succeeded
            remember_reading(self.lake, *pending.remember)
        return op

    async def commit(self, request: Request) -> JSONResponse:
        token = (await request.json())["token"]
        pending = self.pending.pop(token, None)
        if pending is None:
            raise ValueError("preview expired; upload the file again")
        return JSONResponse({"op_id": self._commit_one(pending)})

    async def import_prices(self, request: Request) -> JSONResponse:
        _, raw = await _upload(request)
        return JSONResponse({"op_id": market_import.import_prices(self.lake, raw)})


def _opt_float(value) -> float | None:
    return None if value in (None, "") else float(value)


async def _upload(request: Request) -> tuple[dict, bytes]:
    """Read one uploaded file into memory; it is never spooled to a plaintext temp file."""
    if int(request.headers.get("content-length") or MAX_UPLOAD + 1) > MAX_UPLOAD:
        raise ValueError("file is larger than 20 MB")
    async with request.form(max_files=1, max_fields=20) as form:
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ValueError("no file uploaded")
        raw = await upload.read()
        return {k: v for k, v in form.items() if isinstance(v, str)}, raw
