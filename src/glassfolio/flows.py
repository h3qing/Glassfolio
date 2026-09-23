"""Cash flows inferred from statement differences, and the inbox that asks about
the ones that can't be explained (spec §2.4, §4.3).

No transactions are imported. After each statement, the change not explained by
prices and dividends is either recorded (small), classified by a saved rule, or
turned into an inbox question. Flows are dated to the statement date, so returns
are approximate; more frequent statements make them more accurate.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from importlib import resources

import duckdb

from glassfolio.lake import Lake, OpMeta, RowCounts, new_id, run_write, utc_now

MIN_THRESHOLD = 500.0
REL_THRESHOLD = 0.01
CLASSIFICATIONS = ("deposit", "withdrawal", "transfer", "dividend", "not_a_flow")
PAIR_DAYS = 7


@cache
def _sql() -> str:
    return resources.files("glassfolio").joinpath("sql", "unexplained_flow.sql").read_text()


@dataclass(frozen=True)
class CashFlow:
    flow_id: str
    account_id: str
    date: date
    amount: float
    type: str
    source: str  # inferred | user_confirmed | rule
    rule_id: str | None
    paired_flow_id: str | None


@dataclass(frozen=True)
class InboxItem:
    item_id: str
    type: str
    payload: dict
    status: str
    created_at: datetime
    pair_candidates: tuple[dict, ...] = ()


def _previous_statement(con, account_id: str, as_of: date) -> tuple[str, date] | None:
    return con.execute(
        """SELECT file_hash, as_of_date FROM import_files
           WHERE kind = 'statement' AND status = 'imported' AND account_id = ? AND as_of_date < ?
           ORDER BY as_of_date DESC, imported_at DESC LIMIT 1""", [account_id, as_of]).fetchone()


def _rule_for(con, account_id: str, amount: float) -> tuple[str, str] | None:
    pattern = "in" if amount > 0 else "out"
    return con.execute(
        """SELECT rule_id, classification FROM flow_rules WHERE account_id = ? AND pattern = ?
           ORDER BY created_at DESC LIMIT 1""", [account_id, pattern]).fetchone()


def _insert_flow(con, account_id, day, amount, type_, source, rule_id=None, flow_id=None,
                 paired=None) -> str:
    flow_id = flow_id or new_id("flow")
    con.execute("INSERT INTO cash_flows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [flow_id, account_id, day, f"{amount:.2f}", type_, source, rule_id, paired, utc_now()])
    return flow_id


def _insert_item(con, item_id, type_, payload, status, created_at, resolved_at=None) -> None:
    con.execute("INSERT INTO inbox_items VALUES (?, ?, ?, ?, ?, ?)",
                [item_id, type_, json.dumps(payload), status, created_at, resolved_at])


def infer_after_statement(con: duckdb.DuckDBPyConnection, account_id: str, nickname: str,
                          end_hash: str, end_date: date) -> RowCounts:
    """Runs inside the statement import transaction."""
    prev = _previous_statement(con, account_id, end_date)
    if prev is None:
        return RowCounts()  # the first statement is the starting balance
    start_hash, start_date = prev
    end_value, start_value, dividends, unpriced = con.execute(_sql(), {
        "account_id": account_id, "start_hash": start_hash, "end_hash": end_hash,
        "start_date": start_date, "end_date": end_date}).fetchone()
    base = {"account_id": account_id, "account": nickname,
            "start": start_date.isoformat(), "end": end_date.isoformat()}
    if unpriced:
        _insert_item(con, new_id("inbox"), "data_gap", {**base, "message":
                     f"{unpriced} sold holdings have no price, so the cash flow can't be worked out"},
                     "open", utc_now())
        return RowCounts(inserted=1)
    amount = round(end_value - start_value - dividends, 2)
    if abs(amount) < 0.01:
        return RowCounts()
    rule = _rule_for(con, account_id, amount)
    if rule is not None:
        _insert_flow(con, account_id, end_date, amount, rule[1], "rule", rule_id=rule[0])
    elif abs(amount) < max(MIN_THRESHOLD, REL_THRESHOLD * abs(end_value)):
        _insert_flow(con, account_id, end_date, amount,
                     "deposit" if amount > 0 else "withdrawal", "inferred")
    else:
        _insert_item(con, new_id("inbox"), "unexplained_flow",
                     {**base, "amount": amount, "end_value": end_value}, "open", utc_now())
    return RowCounts(inserted=1)


_OPEN_SQL = """SELECT * FROM inbox_items QUALIFY row_number() OVER (
    PARTITION BY item_id ORDER BY coalesce(resolved_at, created_at) DESC, status = 'open') = 1"""


def _items(con, status: str | None) -> tuple[InboxItem, ...]:
    where = "WHERE status = ?" if status else ""
    rows = con.execute(f"SELECT * FROM ({_OPEN_SQL}) {where} ORDER BY created_at",
                       [status] if status else []).fetchall()
    return tuple(InboxItem(r[0], r[1], json.loads(r[2]), r[3], r[4]) for r in rows)


def _pairs(item: InboxItem, others: tuple[InboxItem, ...]) -> tuple[dict, ...]:
    if item.type != "unexplained_flow":
        return ()
    a, end = item.payload["amount"], date.fromisoformat(item.payload["end"])
    return tuple(
        {"item_id": o.item_id, "account": o.payload["account"], "amount": o.payload["amount"]}
        for o in others
        if o.item_id != item.item_id and o.type == "unexplained_flow"
        and o.payload["account_id"] != item.payload["account_id"]
        and abs(a + o.payload["amount"]) <= max(1.0, 0.01 * abs(a))
        and abs((date.fromisoformat(o.payload["end"]) - end).days) <= PAIR_DAYS)


def list_inbox(lake: Lake, status: str | None = "open") -> tuple[InboxItem, ...]:
    items = _items(lake.con, status)
    open_items = items if status == "open" else _items(lake.con, "open")
    return tuple(InboxItem(i.item_id, i.type, i.payload, i.status, i.created_at,
                           _pairs(i, open_items)) for i in items)


def list_cash_flows(lake: Lake) -> tuple[CashFlow, ...]:
    rows = lake.con.execute(
        """SELECT flow_id, account_id, date, amount::DOUBLE, type, source, rule_id, paired_flow_id
           FROM cash_flows QUALIFY row_number() OVER (PARTITION BY flow_id ORDER BY created_at DESC) = 1
           ORDER BY date, flow_id""").fetchall()
    return tuple(CashFlow(*r) for r in rows)


def resolve_flow(lake: Lake, item_id: str, classification: str, paired_item_id: str | None = None,
                 remember: bool = False, actor: str = "user") -> str:
    """Answer an unexplained-flow question. `remember` saves a rule for this account."""
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {CLASSIFICATIONS}")
    items = {i.item_id: i for i in list_inbox(lake)}
    item = items.get(item_id)
    if item is None:
        raise ValueError("this question is already resolved or does not exist")
    pair = None
    if classification == "transfer":
        pair = items.get(paired_item_id or "")
        if pair is None or pair.item_id not in {c["item_id"] for c in item.pair_candidates}:
            raise ValueError("a transfer needs the matching question from the other account")

    def work(con):
        now = utc_now()
        p = item.payload
        n = 1
        if pair is not None:
            ids = (new_id("flow"), new_id("flow"))
            for flow_id, other, it in ((ids[0], ids[1], item), (ids[1], ids[0], pair)):
                q = it.payload
                _insert_flow(con, q["account_id"], date.fromisoformat(q["end"]), q["amount"],
                             "transfer", "user_confirmed", flow_id=flow_id, paired=other)
                _insert_item(con, it.item_id, it.type, q, "resolved", it.created_at, now)
            n = 4
        else:
            if classification != "not_a_flow":
                _insert_flow(con, p["account_id"], date.fromisoformat(p["end"]), p["amount"],
                             classification, "user_confirmed")
            _insert_item(con, item.item_id, item.type, p, "resolved", item.created_at, now)
            n = 2
        if remember and classification not in ("transfer",):
            con.execute("INSERT INTO flow_rules VALUES (?, ?, ?, ?, ?)",
                        [new_id("rule"), p["account_id"], "in" if p["amount"] > 0 else "out",
                         classification, now])
            n += 1
        return None, RowCounts(inserted=n)

    params = {"item_id": item_id, "classification": classification, "remember": remember}
    return run_write(lake, OpMeta(actor, "resolve_flow", params, "answer a cash flow question"),
                     work)[1]
