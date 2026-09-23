"""The assistant's tools: narrow, typed, and the only way the model reaches data.

Read tools return numbers computed by SQL. Write tools never act: they return a
proposal that runs only after the person clicks Confirm (spec §8).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from glassfolio import registry
from glassfolio.attribution import company_attribution
from glassfolio.exposure import Slice, company_exposure, portfolio_summary
from glassfolio.flows import CLASSIFICATIONS, list_inbox
from glassfolio.lake import Lake
from glassfolio.performance import returns
from glassfolio.recon import run_checks
from glassfolio.tax import company_after_tax, portfolio_after_tax

from glassfolio.assistant.sql_sandbox import describe_tables, query_readonly

MAX_ROWS = 25
FILTERS = {"owner": "person's name", "account_type": "taxable | traditional_ira | roth_ira | 401k | hsa | 529",
           "broker": "broker name", "account": "account nickname"}
GROUP_BYS = ("account", "owner", "fund", "account_type", "broker")


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Context:
    lake: Lake
    today: date  # default as-of date (latest statement)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: dict  # name → description (all optional unless listed in required)
    handler: Callable[[Context, dict], dict]
    required: tuple[str, ...] = ()
    writes: bool = False


def _r(x):
    return None if x is None else round(float(x), 2)


def _date(args: dict, key: str, default: date) -> date:
    raw = args.get(key)
    if raw in (None, ""):
        return default
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        raise ToolError(f"{key} must be a date like 2026-09-30") from None


_TYPE_ALIASES = {"roth": "roth_ira", "roth ira": "roth_ira", "traditional ira": "traditional_ira", "ira": "traditional_ira",
                 "401(k)": "401k", "401 k": "401k", "brokerage": "taxable", "individual": "taxable", "529 plan": "529"}


def _canonical(value: str, choices, label: str, aliases: dict | None = None) -> str:
    """Match a filter value to a real one (case-insensitive, friendly spellings), or explain the choices."""
    wanted = value.strip().lower()
    wanted = (aliases or {}).get(wanted, wanted)
    for choice in choices:
        if choice.lower() == wanted or choice.lower().replace("_", " ") == wanted:
            return choice
    raise ToolError(f"unknown {label} {value!r}; choose one of: {', '.join(sorted(choices)) or 'none yet'}")


def _slice(args: dict, ctx: "Context | None" = None) -> Slice:
    """Filters that match nothing would silently return zeros; refuse them instead."""
    if ctx is None:
        return Slice(*(str(args[k]) if args.get(k) else None for k in ("owner", "account_type", "broker", "account")))
    accounts = registry.list_accounts(ctx.lake.con)
    choices = {"owner": registry.list_owners(ctx.lake.con), "account_type": registry.ACCOUNT_TYPES,
               "broker": {a.broker for a in accounts}, "account": {a.nickname for a in accounts}}
    return Slice(*(_canonical(str(args[k]), choices[k], k.replace("_", " "), _TYPE_ALIASES if k == "account_type" else None)
                   if args.get(k) else None for k in ("owner", "account_type", "broker", "account")))


def portfolio(ctx: Context, a: dict) -> dict:
    as_of, s = _date(a, "as_of", ctx.today), _slice(a, ctx)
    summary, after = portfolio_summary(ctx.lake, as_of, s), portfolio_after_tax(ctx.lake, as_of, s)
    held_via = sum(c.via_fund_value for c in company_exposure(ctx.lake, as_of, slice_=s))
    return {"as_of": as_of.isoformat(), "total_value": _r(summary.total_value), "after_tax_value": _r(after.after_tax),
            "estimated_tax": _r(after.tax), "cash": _r(summary.cash_value), "held_through_funds": _r(held_via),
            "approximate_value": _r(summary.approx_value), "securities_missing_price": summary.missing_prices}


def exposure(ctx: Context, a: dict) -> dict:
    as_of, s = _date(a, "as_of", ctx.today), _slice(a, ctx)
    group_by = a.get("group_by") or None
    if group_by not in (None, *GROUP_BYS):
        raise ToolError(f"group_by must be one of {GROUP_BYS}")
    rows = company_exposure(ctx.lake, as_of, a.get("ticker") or None, group_by, s)
    taxed = {(t.ticker, t.name): t.after_tax for t in company_after_tax(ctx.lake, as_of, s)} if not group_by else {}
    total = portfolio_summary(ctx.lake, as_of, s).total_value
    return {"as_of": as_of.isoformat(), "portfolio_value": _r(total), "rows": [
        {"ticker": r.ticker, "name": r.name, **({"group": r.group} if group_by else {}),
         "held_directly": _r(r.direct_value), "through_funds": _r(r.via_fund_value), "total": _r(r.total),
         "share_of_portfolio_pct": _r(100 * r.total / total) if total else None,
         **({"after_tax": _r(taxed.get((r.ticker, r.name)))} if taxed else {}),
         "approximate": r.approx} for r in rows[:MAX_ROWS]], "more_rows": max(0, len(rows) - MAX_ROWS)}


def changes(ctx: Context, a: dict) -> dict:
    end = _date(a, "end", ctx.today)
    start = _date(a, "start", end - timedelta(days=30))
    if start > end:
        raise ToolError("start must be before end")
    s = _slice(a, ctx)
    r = returns(ctx.lake, start, end, s)
    rows = [x for x in company_attribution(ctx.lake, start, end, s) if not a.get("ticker")
            or (x.ticker or "").upper() == str(a["ticker"]).upper()]
    return {"start": start.isoformat(), "end": end.isoformat(),
            "start_value": _r(r.start_value), "end_value": _r(r.end_value), "money_added": _r(r.net_flows),
            "time_weighted_return_pct": _r(100 * r.twr) if r.twr is not None else None,
            "money_weighted_return_pct": _r(100 * r.mwr_period) if r.mwr_period is not None else None,
            "unanswered_questions": r.open_questions,
            "companies": [{"ticker": x.ticker, "start": _r(x.start_value), "end": _r(x.end_value),
                           "price_effect": _r(x.price_effect), "your_money": _r(x.flow_effect),
                           "fund_rebalancing": _r(x.rebalance_effect), "change": _r(x.change)}
                          for x in rows[:MAX_ROWS]]}


def accounts(ctx: Context, a: dict) -> dict:
    return {"people": list(registry.list_owners(ctx.lake.con)), "accounts": [
        {"nickname": x.nickname, "owner": x.owner, "account_type": x.account_type, "broker": x.broker,
         "latest_statement": x.latest_statement.isoformat() if x.latest_statement else None}
        for x in registry.list_accounts(ctx.lake.con)]}


def questions(ctx: Context, a: dict) -> dict:
    return {"questions": [
        {"item_id": i.item_id, "type": i.type, "account": i.payload.get("account"),
         "amount": _r(i.payload.get("amount")), "period": [i.payload.get("start"), i.payload.get("end")],
         "message": i.payload.get("message"),
         "possible_transfer_pairs": [{"item_id": c["item_id"], "account": c["account"]} for c in i.pair_candidates]}
        for i in list_inbox(ctx.lake)]}


def checks(ctx: Context, a: dict) -> dict:
    as_of = _date(a, "as_of", ctx.today)
    account_id = None
    if a.get("account"):
        acct = registry.find_account(ctx.lake.con, str(a["account"]))
        if acct is None:
            raise ToolError(f"unknown account: {a['account']}")
        account_id = acct.account_id
    num = lambda k: None if a.get(k) in (None, "") else float(str(a[k]).replace(",", "").replace("$", ""))  # noqa: E731
    report = run_checks(ctx.lake, as_of, account_id, num("reported_total"), num("reported_cost"), store=False)
    return {"scope": a.get("account") or "whole portfolio", "as_of": as_of.isoformat(), "status": report.status,
            "checks": [{"check": r.check_type, "status": r.status, "broker": _r(r.expected), "glassfolio": _r(r.actual),
                        "hint": r.hint} for r in report.results]}


def taxes(ctx: Context, a: dict) -> dict:
    as_of, s = _date(a, "as_of", ctx.today), _slice(a, ctx)
    t = portfolio_after_tax(ctx.lake, as_of, s)
    return {"as_of": as_of.isoformat(), "before_tax": _r(t.pre_tax), "estimated_tax": _r(t.tax),
            "after_tax": _r(t.after_tax), "holdings_without_cost_basis": t.missing_cost,
            "by_treatment": {k: {"before_tax": _r(v["pre_tax"]), "tax": _r(v["tax"])} for k, v in t.by_treatment.items()},
            "note": "planning estimate from the saved assumptions, not tax advice"}


def sql(ctx: Context, a: dict) -> dict:
    if not a.get("sql"):
        raise ToolError("sql is required")
    return query_readonly(ctx.lake, str(a["sql"]), _date(a, "as_of", ctx.today))


def answer_question(ctx: Context, a: dict) -> dict:
    """Write tool: only builds the proposal; the agent turns it into a pending action."""
    items = {i.item_id: i for i in list_inbox(ctx.lake)}
    item = items.get(str(a.get("item_id")))
    if item is None:
        raise ToolError("unknown or already answered question; call list_questions")
    if a.get("classification") not in CLASSIFICATIONS:
        raise ToolError(f"classification must be one of {CLASSIFICATIONS}")
    if item.type != "unexplained_flow" and a["classification"] != "not_a_flow":
        raise ToolError("this question can only be marked as checked (not_a_flow)")
    words = {"deposit": "money added", "withdrawal": "money taken out", "transfer": "a transfer between accounts",
             "dividend": "dividends or interest", "not_a_flow": "not a cash flow"}
    remember = a.get("remember") is True or str(a.get("remember")).lower() == "true"
    pair = items.get(str(a.get("pair_item_id") or ""))
    if a["classification"] == "transfer" and pair is None:
        raise ToolError("a transfer needs pair_item_id: the other account's question (see possible_transfer_pairs)")
    amount = item.payload.get("amount")
    shown = f" ({'+' if amount > 0 else '−'}${abs(amount):,.2f})" if isinstance(amount, (int, float)) else ""
    return {"proposal": f"Mark {item.payload.get('account', 'this')}{shown}, {item.payload.get('start')} to "
                        f"{item.payload.get('end')}, as {words[a['classification']]}"
                        + (f" with {pair.payload.get('account')}" if pair else "")
                        + (", and treat future ones in this account the same way" if remember else ""),
            "args": {"item_id": item.item_id, "classification": a["classification"],
                     "paired_item_id": pair.item_id if pair else None, "remember": remember}}


def request_file(ctx: Context, a: dict) -> dict:
    return {"card": "file_request", "what": str(a.get("what") or "a file"), "where": str(a.get("where") or "")}


_F = {k: f"optional filter: {v}" for k, v in FILTERS.items()}
TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("portfolio_summary", "Total value, after-tax value, cash and look-through overview.",
         {"as_of": "YYYY-MM-DD (default: latest)", **_F}, portfolio),
    Tool("get_exposure", "Real exposure per company after looking through funds; one ticker or all; "
         f"optionally grouped by {', '.join(GROUP_BYS)}.",
         {"ticker": "e.g. NVDA", "group_by": " | ".join(GROUP_BYS), "as_of": "YYYY-MM-DD", **_F}, exposure),
    Tool("get_changes", "Returns (time- and money-weighted) and why each holding changed between two dates: "
         "price, your money, fund rebalancing.", {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "ticker": "optional", **_F},
         changes),
    Tool("list_accounts", "People, their accounts, types, brokers and latest statement dates.", {}, accounts),
    Tool("list_questions", "Open questions: unexplained cash flows, data gaps, price anomalies.", {}, questions),
    Tool("run_checks", "Reconcile an account (or everything) against the total the broker shows "
         "(looks only; nothing is saved).",
         {"account": "nickname", "as_of": "YYYY-MM-DD", "reported_total": "number the broker shows",
          "reported_cost": "optional"}, checks),
    Tool("tax_summary", "Before-tax, estimated tax and after-tax value by treatment.",
         {"as_of": "YYYY-MM-DD", **_F}, taxes),
    Tool("query_readonly", "Read-only SQL (one SELECT, max 200 rows) over these tables:\n" + describe_tables(),
         {"sql": "SELECT ...", "as_of": "date for the exposure table"}, sql, required=("sql",)),
    Tool("answer_question", "Propose an answer to an open question; the person must confirm it.",
         {"item_id": "from list_questions", "classification": " | ".join(CLASSIFICATIONS),
          "pair_item_id": "the other account's question, for a transfer", "remember": "true to apply to future ones"},
         answer_question, required=("item_id", "classification"), writes=True),
    Tool("request_user_file", "Ask the person to export and drop a file (e.g. a statement or fund holdings).",
         {"what": "what to download", "where": "where to find it"}, request_file),
)}


def run_tool(ctx: Context, name: str, args: dict) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"unknown tool {name!r}; available: {', '.join(TOOLS)}")
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    missing = [p for p in tool.required if args.get(p) in (None, "")]
    if missing:
        raise ToolError(f"missing arguments: {', '.join(missing)}")
    return tool.handler(ctx, args)
