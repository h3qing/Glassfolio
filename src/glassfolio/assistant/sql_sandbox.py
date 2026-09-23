"""query_readonly: free SQL for capable local models, in a sandbox (spec §8).

The query never touches the real lake. It runs in a fresh in-memory DuckDB with
external access off and configuration locked (no files, no network, no env),
holding copies of a few allow-listed tables — never keys, raw imported files or
the audit log. It runs in a child process that is killed at the time limit, since
DuckDB can't interrupt every operation or bound every allocation. On top: the SQL
must parse as one SELECT with no table functions, and results are capped in rows.
"""

import json
import multiprocessing
from datetime import date

import duckdb

from glassfolio.exposure import exposure_lines
from glassfolio.flows import list_cash_flows
from glassfolio.lake import Lake

MAX_ROWS = 200
TIMEOUT_S = 5.0
STARTUP_S = 3.0  # starting the child process
SANDBOX = {"enable_external_access": False, "autoload_known_extensions": False,
           "autoinstall_known_extensions": False, "memory_limit": "512MB", "threads": 1,
           "max_temp_directory_size": "0B", "lock_configuration": True}

# Table → (description for the model, SQL on the real lake that produces it)
TABLES = {
    "accounts": ("account_id, nickname, owner, account_type, broker (latest)", """
        SELECT a.account_id, a.nickname, o.nickname AS owner, a.account_type, a.broker
        FROM (SELECT * FROM accounts QUALIFY row_number() OVER (PARTITION BY account_id ORDER BY created_at DESC) = 1) a
        LEFT JOIN (SELECT * FROM owners QUALIFY row_number() OVER (PARTITION BY owner_id ORDER BY created_at DESC) = 1) o
          USING (owner_id)"""),
    "securities": ("security_id, ticker, name, type (latest)", """
        SELECT security_id, ticker, name, type FROM securities
        QUALIFY row_number() OVER (PARTITION BY security_id ORDER BY created_at DESC) = 1"""),
    "positions": ("account_id, security_id, shares, cost_basis, export_price, as_of_date (every statement)", """
        SELECT account_id, security_id, shares::DOUBLE AS shares, cost_basis::DOUBLE AS cost_basis,
               export_price::DOUBLE AS export_price, as_of_date FROM positions"""),
    "prices": ("security_id, date, close, dividend_per_share, source", """
        SELECT security_id, date, close::DOUBLE AS close, dividend_per_share::DOUBLE AS dividend_per_share, source
        FROM prices"""),
    "etf_holdings": ("etf_id, holding_id, as_of_date, shares, weight (fund holdings versions)", """
        SELECT etf_id, holding_id, as_of_date, shares::DOUBLE AS shares, weight FROM etf_holdings"""),
    "recon_results": ("scope, check_type, as_of_date, expected, actual, status, hint, run_at", """
        SELECT scope, check_type, as_of_date, expected, actual, status, hint, run_at FROM recon_results"""),
}
DERIVED = {
    "exposure": "account_id, security_id, via_security_id (NULL = held directly), kind "
                "(position | security | cash | other | fund_residual), shares, price, value, approx — "
                "look-through at the requested date; sum value over kind='security' per security for exposure",
    "cash_flows": "account_id, date, amount, type (deposit | withdrawal | transfer | dividend), source",
}


class SandboxError(ValueError):
    pass


def describe_tables() -> str:
    lines = [f"{name}({desc})" for name, (desc, _) in TABLES.items()]
    lines += [f"{name}({desc})" for name, desc in DERIVED.items()]
    return "\n".join(lines)


def _load(box: duckdb.DuckDBPyConnection, name: str, columns: list[str], rows: list[tuple]) -> None:
    if not rows:
        box.execute(f"CREATE TABLE {name} ({', '.join(f'{c} VARCHAR' for c in columns)})")
        return
    params = {f"c{i}": [r[i] for r in rows] for i in range(len(columns))}
    select = ", ".join(f"unnest($c{i}) AS {c}" for i, c in enumerate(columns))
    box.execute(f"CREATE TABLE {name} AS SELECT {select}", params)


def _snapshot(lake: Lake, as_of: date) -> tuple[tuple[str, list[str], list[tuple]], ...]:
    """Copies of the allow-listed tables, taken in the main process."""
    out = []
    for name, (_, sql) in TABLES.items():
        cursor = lake.con.execute(sql)
        out.append((name, [d[0] for d in cursor.description], cursor.fetchall()))
    lines = exposure_lines(lake, as_of)
    out.append(("exposure", ["account_id", "security_id", "via_security_id", "kind", "shares", "price", "value", "approx"],
                [(l.account_id, l.security_id, l.via_security_id, l.kind, l.shares, l.price, l.value, l.approx)
                 for l in lines]))
    out.append(("cash_flows", ["account_id", "date", "amount", "type", "source"],
                [(f.account_id, f.date, f.amount, f.type, f.source) for f in list_cash_flows(lake)]))
    return tuple(out)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def check_sql(box: duckdb.DuckDBPyConnection, sql: str) -> str:
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise SandboxError("empty query")
    tree = json.loads(box.execute("SELECT json_serialize_sql(?)", [text]).fetchone()[0])
    if tree.get("error") or len(tree.get("statements", [])) != 1:
        raise SandboxError("only a single SELECT statement is allowed")
    if any(n.get("type") == "TABLE_FUNCTION" for n in _walk(tree)):
        raise SandboxError("table functions are not allowed; query the tables listed")
    return text


def _child(tables, sql: str, conn) -> None:
    """Runs in a separate process: a runaway query can be killed without hurting the app."""
    try:
        box = duckdb.connect(":memory:", config=dict(SANDBOX))
        for name, columns, rows in tables:
            _load(box, name, columns, rows)
        text = check_sql(box, sql)
        cursor = box.execute(f"SELECT * FROM ({text}) LIMIT {MAX_ROWS + 1}")
        conn.send(("ok", [d[0] for d in cursor.description], cursor.fetchall()))
    except SandboxError as exc:
        conn.send(("error", str(exc), None))
    except duckdb.Error as exc:
        conn.send(("error", str(exc).split("\n")[0][:300], None))
    finally:
        conn.close()


def query_readonly(lake: Lake, sql: str, as_of: date) -> dict:
    tables = _snapshot(lake, as_of)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(tables, sql, child), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(TIMEOUT_S + STARTUP_S):
            raise SandboxError(f"the query took longer than {TIMEOUT_S:.0f} seconds and was stopped")
        status, first, rows = parent.recv()
    except EOFError:
        raise SandboxError("the query used too many resources and was stopped") from None
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(1)
    if status != "ok":
        raise SandboxError(first)
    return {"columns": first, "rows": [list(r) for r in rows[:MAX_ROWS]], "truncated": len(rows) > MAX_ROWS}
