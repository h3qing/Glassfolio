"""Daily portfolio snapshots (portfolio_daily): one row per account × security × kind.

Append-only and idempotent: a day is computed once unless `rebuild` is asked for,
which appends a newer computation that supersedes the old one.
"""

from datetime import date
from itertools import groupby

from glassfolio.exposure import LOOK_THROUGH_KINDS, Slice, accounts_in_slice, exposure_lines
from glassfolio.lake import Lake, OpMeta, RowCounts, insert_rows, run_write, utc_now

_LATEST = """SELECT * FROM portfolio_daily QUALIFY computed_at = max(computed_at) OVER (PARTITION BY date)"""


def _confidence(con, d: date) -> dict[str, str]:
    """Worst status of each account's latest reconciliation on or before d."""
    rows = con.execute(
        """SELECT scope, CASE max(CASE status WHEN 'fail' THEN 2 WHEN 'warn' THEN 1 ELSE 0 END)
               WHEN 2 THEN 'fail' WHEN 1 THEN 'warn' ELSE 'pass' END
           FROM (SELECT * FROM recon_results WHERE as_of_date <= ?
                 QUALIFY run_at = max(run_at) OVER (PARTITION BY scope))
           GROUP BY scope""", [d]).fetchall()
    return dict(rows)


def snapshot_day(lake: Lake, d: date, rebuild: bool = False, actor: str = "user") -> str | None:
    exists = lake.con.execute("SELECT count(*) FROM portfolio_daily WHERE date = ?", [d]).fetchone()[0]
    if exists and not rebuild:
        return None
    lines = [l for l in exposure_lines(lake, d) if l.kind in LOOK_THROUGH_KINDS]
    owners = dict(lake.con.execute(
        "SELECT account_id, owner_id FROM accounts QUALIFY row_number() OVER ("
        "PARTITION BY account_id ORDER BY created_at DESC) = 1").fetchall())
    confidence = _confidence(lake.con, d)
    key = lambda l: (l.account_id, l.security_id, l.kind)  # noqa: E731
    now = utc_now()
    rows = tuple(
        (d, acct, owners.get(acct), sec, kind,
         sum(l.value or 0 for l in group if l.via_security_id is None),
         sum(l.value or 0 for l in group if l.via_security_id is not None),
         confidence.get(f"account:{acct}", "unchecked"), now)
        for (acct, sec, kind), group in ((k, tuple(g)) for k, g in groupby(sorted(lines, key=key), key)))

    def work(con):
        return None, RowCounts(inserted=insert_rows(con, "portfolio_daily", rows))

    meta = OpMeta(actor, "snapshot_day", {"as_of": d.isoformat(), "rebuild": rebuild},
                  "store daily portfolio snapshot")
    return run_write(lake, meta, work)[1]


def value_history(lake: Lake, slice_: Slice = Slice()) -> tuple[tuple[date, float], ...]:
    accounts = list(accounts_in_slice(lake, slice_))
    rows = lake.con.execute(
        f"""SELECT date, sum(direct_value + via_fund_value) FROM ({_LATEST})
            WHERE list_contains(?, account_id) GROUP BY date ORDER BY date""", [accounts]).fetchall()
    return tuple((r[0], r[1]) for r in rows)
