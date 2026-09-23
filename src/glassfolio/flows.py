"""Cash flows inferred from statement differences, and the questions asked about
the ones that can't be explained (spec §2.4, §4.3).

Nothing about flows is computed at import time. Each pair of consecutive
statements for an account (a "period") is re-evaluated on every read against the
current data, so corrected statements, late dividends, newly fetched prices and
out-of-order imports can't leave stale flows behind. Only the user's answers and
rules are stored.

Per period, the change that prices and dividends don't explain is:
- classified by the user's answer for that period, if any;
- else by a remembered rule for that account and direction;
- else recorded as `inferred` when small (< max($500, 1% of value));
- else asked about in the inbox.
Flows are dated to the statement date, so returns are approximate.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from importlib import resources

from glassfolio.lake import Lake, OpMeta, RowCounts, new_id, run_write, utc_now

MIN_THRESHOLD = 500.0
REL_THRESHOLD = 0.01
CLASSIFICATIONS = ("deposit", "withdrawal", "transfer", "dividend", "not_a_flow")
EXTERNAL = ("deposit", "withdrawal", "transfer")
PAIR_DAYS = 7


@cache
def _sql() -> str:
    return resources.files("glassfolio").joinpath("sql", "unexplained_flow.sql").read_text()


@dataclass(frozen=True)
class Period:
    account_id: str
    account: str
    start: date
    start_hash: str
    end: date
    end_hash: str

    @property
    def key(self) -> str:
        return f"{self.account_id}:{self.start}:{self.end}"


@dataclass(frozen=True)
class PeriodResult:
    period: Period
    amount: float | None      # None when a sold holding has no later price
    end_value: float
    unpriced: int


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
    type: str  # unexplained_flow | data_gap | price_review
    payload: dict
    status: str
    created_at: datetime | None
    pair_candidates: tuple[dict, ...] = ()


def periods(con) -> tuple[Period, ...]:
    """Consecutive statements per account; the latest import wins for a given date."""
    rows = con.execute("""
        WITH eff AS (
            SELECT account_id, as_of_date, file_hash FROM import_files
            WHERE kind = 'statement' AND status = 'imported'
            QUALIFY row_number() OVER (PARTITION BY account_id, as_of_date ORDER BY imported_at DESC) = 1),
        names AS (SELECT account_id, nickname FROM accounts QUALIFY row_number() OVER (
            PARTITION BY account_id ORDER BY created_at DESC) = 1)
        SELECT e.account_id, n.nickname, lag(e.as_of_date) OVER w, lag(e.file_hash) OVER w,
               e.as_of_date, e.file_hash
        FROM eff e JOIN names n USING (account_id)
        WINDOW w AS (PARTITION BY e.account_id ORDER BY e.as_of_date)
        QUALIFY lag(e.as_of_date) OVER w IS NOT NULL
        ORDER BY e.as_of_date, n.nickname""").fetchall()
    return tuple(Period(*r) for r in rows)


def evaluate(con, p: Period) -> PeriodResult:
    end_value, start_value, dividends, unpriced = con.execute(_sql(), {
        "account_id": p.account_id, "start_hash": p.start_hash, "end_hash": p.end_hash,
        "start_date": p.start, "end_date": p.end}).fetchone()
    if unpriced:
        return PeriodResult(p, None, end_value or 0.0, unpriced)
    return PeriodResult(p, round((end_value or 0) - (start_value or 0) - (dividends or 0), 2),
                        end_value or 0.0, 0)


def _answers(con) -> dict[str, tuple[str, str | None]]:
    rows = con.execute("""SELECT item_id, classification, paired_item_id FROM flow_answers
        QUALIFY row_number() OVER (PARTITION BY item_id ORDER BY created_at DESC) = 1""").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _rules(con) -> dict[tuple[str, str], tuple[str, str]]:
    rows = con.execute("""SELECT account_id, pattern, rule_id, classification FROM flow_rules
        QUALIFY row_number() OVER (PARTITION BY account_id, pattern ORDER BY created_at DESC) = 1""").fetchall()
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def _flow_item_id(p: Period) -> str:
    return f"flow:{p.key}"


def _gap_item_id(p: Period) -> str:
    return f"gap:{p.key}"


@dataclass(frozen=True)
class _State:
    results: tuple[PeriodResult, ...]
    answers: dict
    rules: dict


def _state(con) -> _State:
    return _State(tuple(evaluate(con, p) for p in periods(con)), _answers(con), _rules(con))


def _is_small(r: PeriodResult) -> bool:
    return abs(r.amount) < max(MIN_THRESHOLD, REL_THRESHOLD * abs(r.end_value))


def list_cash_flows(lake: Lake) -> tuple[CashFlow, ...]:
    s = _state(lake.con)
    flows = []
    for r in s.results:
        if r.amount is None or abs(r.amount) < 0.01:
            continue
        p, item_id = r.period, _flow_item_id(r.period)
        answer = s.answers.get(item_id)
        rule = s.rules.get((p.account_id, "in" if r.amount > 0 else "out"))
        if answer is not None:
            kind, paired = answer
            if kind != "not_a_flow":
                flows.append(CashFlow(item_id, p.account_id, p.end, r.amount, kind, "user_confirmed",
                                      None, paired))
        elif rule is not None:
            flows.append(CashFlow(item_id, p.account_id, p.end, r.amount, rule[1], "rule", rule[0], None))
        elif _is_small(r):
            flows.append(CashFlow(item_id, p.account_id, p.end, r.amount,
                                  "deposit" if r.amount > 0 else "withdrawal", "inferred", None, None))
    return tuple(flows)


def _open_questions(s: _State) -> tuple[InboxItem, ...]:
    items = []
    for r in s.results:
        p = r.period
        base = {"account_id": p.account_id, "account": p.account,
                "start": p.start.isoformat(), "end": p.end.isoformat()}
        if r.amount is None:
            if _gap_item_id(p) not in s.answers:
                items.append(InboxItem(_gap_item_id(p), "data_gap", {**base, "message":
                    f"{p.account}: {r.unpriced} holding(s) sold between {p.start} and {p.end} have no "
                    "later price, so money in or out can't be worked out. Import closing prices."},
                    "open", None))
        elif (abs(r.amount) >= 0.01 and not _is_small(r) and _flow_item_id(p) not in s.answers
              and (p.account_id, "in" if r.amount > 0 else "out") not in s.rules):
            items.append(InboxItem(_flow_item_id(p), "unexplained_flow",
                                   {**base, "amount": r.amount, "end_value": r.end_value}, "open", None))
    return tuple(items)


def _stored_items(con) -> tuple[InboxItem, ...]:
    rows = con.execute("""SELECT item_id, type, payload, status, created_at FROM inbox_items
        QUALIFY row_number() OVER (PARTITION BY item_id
            ORDER BY coalesce(resolved_at, created_at) DESC, status = 'open') = 1""").fetchall()
    return tuple(InboxItem(r[0], r[1], json.loads(r[2]), r[3], r[4]) for r in rows if r[3] == "open")


def _pairs(item: InboxItem, others: tuple[InboxItem, ...]) -> tuple[dict, ...]:
    if item.type != "unexplained_flow":
        return ()
    a, end = item.payload["amount"], date.fromisoformat(item.payload["end"])
    return tuple(
        {"item_id": o.item_id, "account": o.payload["account"], "amount": o.payload["amount"]}
        for o in others
        if o.type == "unexplained_flow" and o.payload["account_id"] != item.payload["account_id"]
        and abs(a + o.payload["amount"]) <= max(1.0, 0.01 * abs(a))
        and abs((date.fromisoformat(o.payload["end"]) - end).days) <= PAIR_DAYS)


def list_inbox(lake: Lake) -> tuple[InboxItem, ...]:
    questions = _open_questions(_state(lake.con))
    with_pairs = tuple(InboxItem(i.item_id, i.type, i.payload, i.status, i.created_at, _pairs(i, questions))
                       for i in questions)
    return with_pairs + _stored_items(lake.con)


def _answer_rows(item: InboxItem, classification: str, paired: str | None, now) -> tuple:
    p = item.payload
    return (item.item_id, p["account_id"], date.fromisoformat(p["start"]),
            date.fromisoformat(p["end"]), classification, paired, now)


def resolve_flow(lake: Lake, item_id: str, classification: str, paired_item_id: str | None = None,
                 remember: bool = False, actor: str = "user") -> str:
    """Answer a question. Flow questions can be re-answered later to change the answer."""
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {CLASSIFICATIONS}")
    open_items = {i.item_id: i for i in list_inbox(lake)}
    answered = _answers(lake.con)
    item = open_items.get(item_id)
    if item is None and item_id in answered:  # re-answer: rebuild the question from its period
        item = next((InboxItem(_flow_item_id(r.period), "unexplained_flow",
                               {"account_id": r.period.account_id, "account": r.period.account,
                                "start": r.period.start.isoformat(), "end": r.period.end.isoformat(),
                                "amount": r.amount}, "answered", None)
                     for r in _state(lake.con).results if _flow_item_id(r.period) == item_id), None)
    if item is None:
        raise ValueError("this question does not exist or is already resolved")
    if item.type != "unexplained_flow" and classification != "not_a_flow":
        raise ValueError("this question can only be marked as checked")
    pair = None
    if classification == "transfer":
        pair = open_items.get(paired_item_id or "")
        if pair is None or pair.item_id not in {c["item_id"] for c in item.pair_candidates}:
            raise ValueError("a transfer needs the matching question from the other account")

    def work(con):
        now = utc_now()
        n = 0
        if item.type == "price_review":
            con.execute("INSERT INTO inbox_items VALUES (?, ?, ?, 'resolved', ?, ?)",
                        [item.item_id, item.type, json.dumps(item.payload), item.created_at, now])
            return None, RowCounts(inserted=1)
        con.execute("INSERT INTO flow_answers VALUES (?, ?, ?, ?, ?, ?, ?)",
                    list(_answer_rows(item, classification, pair.item_id if pair else None, now)))
        n += 1
        if pair is not None:
            con.execute("INSERT INTO flow_answers VALUES (?, ?, ?, ?, ?, ?, ?)",
                        list(_answer_rows(pair, "transfer", item.item_id, now)))
            n += 1
        if remember and item.type == "unexplained_flow" and classification != "transfer":
            con.execute("INSERT INTO flow_rules VALUES (?, ?, ?, ?, ?)",
                        [new_id("rule"), item.payload["account_id"],
                         "in" if item.payload["amount"] > 0 else "out", classification, now])
            n += 1
        return None, RowCounts(inserted=n)

    params = {"item_id": item_id, "classification": classification, "remember": remember}
    return run_write(lake, OpMeta(actor, "resolve_flow", params, "answer a question"), work)[1]
