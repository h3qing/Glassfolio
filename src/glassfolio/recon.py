"""Reconciliation checks (spec §11). Numbers are shown only after these pass."""

from dataclasses import dataclass
from datetime import date

from glassfolio.exposure import LOOK_THROUGH_KINDS, exposure_lines
from glassfolio.lake import Lake, OpMeta, RowCounts, new_id, run_write, utc_now

ACCOUNT_TOTAL_TOL = 0.005      # 0.5% of reported total
COST_TOTAL_TOL = 1.0           # dollars
CONSERVATION_TOL = 1e-9        # relative; lines must sum to portfolio value
STATUS_ORDER = ("pass", "warn", "fail")


@dataclass(frozen=True)
class CheckResult:
    check_type: str
    status: str
    expected: float | None = None
    actual: float | None = None
    hint: str | None = None

    @property
    def diff(self) -> float | None:
        if self.expected is None or self.actual is None:
            return None
        return self.actual - self.expected


@dataclass(frozen=True)
class CheckReport:
    scope: str
    as_of: date
    results: tuple[CheckResult, ...]

    @property
    def status(self) -> str:
        return max((r.status for r in self.results), key=STATUS_ORDER.index, default="pass")


@dataclass(frozen=True)
class Statement:
    as_of: date
    rows: tuple[tuple[str, str, float, float | None], ...]  # ticker, type, value, cost


def _statement(lake: Lake, account_id: str, as_of: date) -> Statement | None:
    """Latest imported statement for the account on or before as_of."""
    head = lake.con.execute(
        """SELECT file_hash, as_of_date FROM import_files
           WHERE kind = 'statement' AND status = 'imported' AND account_id = ? AND as_of_date <= ?
           ORDER BY as_of_date DESC, imported_at DESC LIMIT 1""", [account_id, as_of]).fetchone()
    if head is None:
        return None
    rows = lake.con.execute(
        """SELECT s.ticker, s.type, (p.shares * p.export_price)::DOUBLE, p.cost_basis::DOUBLE
           FROM positions p JOIN (SELECT * FROM securities QUALIFY row_number() OVER (
               PARTITION BY security_id ORDER BY created_at DESC) = 1) s USING (security_id)
           WHERE p.import_file_hash = ? AND p.account_id = ?""", [head[0], account_id]).fetchall()
    return Statement(head[1], tuple(rows))


def _total_hint(stmt: Statement, diff: float, tol: float) -> str:
    if not any(t == "cash" for _, t, _, _ in stmt.rows):
        return "statement has no cash row"
    matches = [t for t, _, value, _ in stmt.rows if abs(abs(diff) - abs(value)) <= tol]
    if matches:
        return f"difference matches the {', '.join(matches)} position(s); missing or doubled?"
    return "prices may be from a different time than the broker total"


def check_account_total(stmt: Statement, reported: float) -> CheckResult:
    actual = sum(v for _, _, v, _ in stmt.rows)
    tol = abs(reported) * ACCOUNT_TOTAL_TOL
    ok = abs(actual - reported) <= tol
    hint = None if ok else _total_hint(stmt, actual - reported, tol)
    return CheckResult("account_total", "pass" if ok else "fail", reported, actual, hint)


def check_cost_total(stmt: Statement, reported: float) -> CheckResult:
    actual = sum(c for _, _, _, c in stmt.rows if c is not None)
    ok = abs(actual - reported) <= COST_TOTAL_TOL
    hint = None if ok else "some rows may lack cost basis in the export"
    return CheckResult("cost_total", "pass" if ok else "fail", reported, actual, hint)


def check_statement_date(stmt: Statement, as_of: date) -> CheckResult:
    if stmt.as_of == as_of:
        return CheckResult("statement_date", "pass")
    return CheckResult("statement_date", "warn", hint=f"latest statement is from {stmt.as_of}")


def check_look_through(lake: Lake, as_of: date, account_id: str | None) -> tuple[CheckResult, ...]:
    lines = [l for l in exposure_lines(lake, as_of)
             if account_id is None or l.account_id == account_id]
    missing = sorted({l.security_id for l in lines if l.value is None})
    if missing:
        return (CheckResult("prices", "fail", hint=f"{len(missing)} securities lack a price"),)
    top = sum(l.value for l in lines if l.kind == "position")
    looked = sum(l.value for l in lines if l.kind in LOOK_THROUGH_KINDS)
    ok = abs(top - looked) <= CONSERVATION_TOL * max(abs(top), 1.0)
    results = [CheckResult("conservation", "pass" if ok else "fail", top, looked)]
    if any(l.kind == "truncated" for l in lines):
        results.append(CheckResult("fund_depth", "warn", hint="fund nesting deeper than 5 levels"))
    approx = sum(l.value for l in lines if l.approx and l.kind in LOOK_THROUGH_KINDS)
    if approx:
        results.append(CheckResult("approximation", "warn", None, approx,
                                   "some funds use weights or an index proxy"))
    return tuple(results)


def run_checks(
    lake: Lake, as_of: date, account_id: str | None = None,
    reported_total: float | None = None, reported_cost: float | None = None,
    actor: str = "user",
) -> CheckReport:
    """Run checks for one account (or the whole portfolio) and store the results."""
    results: tuple[CheckResult, ...] = ()
    if account_id is not None:
        stmt = _statement(lake, account_id, as_of)
        if stmt is None:
            results = (CheckResult("statement", "fail", hint="no statement imported"),)
        else:
            results = (check_statement_date(stmt, as_of),)
            if reported_total is not None:
                results += (check_account_total(stmt, reported_total),)
            if reported_cost is not None:
                results += (check_cost_total(stmt, reported_cost),)
    results += check_look_through(lake, as_of, account_id)
    scope = f"account:{account_id}" if account_id else "portfolio"
    report = CheckReport(scope, as_of, results)
    _store(lake, report, actor)
    return report


def _store(lake: Lake, report: CheckReport, actor: str) -> None:
    run_at = utc_now()

    def work(con):
        for r in report.results:
            con.execute("INSERT INTO recon_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [new_id("chk"), report.scope, r.check_type, report.as_of,
                         r.expected, r.actual, r.diff, r.status, r.hint, run_at])
        return None, RowCounts(inserted=len(report.results))

    params = {"scope": report.scope, "as_of": report.as_of.isoformat(), "status": report.status}
    run_write(lake, OpMeta(actor, "run_checks", params, "store reconciliation results"), work)
