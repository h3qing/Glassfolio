"""Encrypted DuckLake storage, the audited write path and restore.

Every write goes through `run_write`, which executes the work in one
transaction, appends an ops_log row and tags the resulting DuckLake snapshot
with the op_id so the log and the snapshot history stay linked.
"""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from decimal import Decimal
from typing import Callable, Mapping, Sequence, TypeVar

import duckdb

from glassfolio.keys import validate_key
from glassfolio.paths import resource_root

CATALOG = "lake"
BUSINESS_TABLES = (
    "owners", "accounts", "securities", "positions", "prices",
    "corporate_actions", "etf_holdings", "import_profiles", "import_files",
    "recon_results", "flow_answers", "flow_rules", "inbox_items", "portfolio_daily",
    "fetch_log", "tax_profiles", "tax_assignments", "position_lots",
)
ACTORS = ("user", "scheduler", "model")

T = TypeVar("T")


@dataclass(frozen=True)
class Lake:
    con: duckdb.DuckDBPyConnection


@dataclass(frozen=True)
class RowCounts:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0

    def __add__(self, other: "RowCounts") -> "RowCounts":
        return RowCounts(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.deleted + other.deleted,
        )


@dataclass(frozen=True)
class OpMeta:
    actor: str
    tool: str
    params: Mapping[str, object]
    description: str


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def open_lake(home: Path, key: str) -> Lake:
    """Open (creating if needed) the encrypted lake under `home`."""
    validate_key(key)
    home.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET temp_file_encryption = true")
    con.execute(f"SET temp_directory = {_sql_str(str(home / 'tmp'))}")
    ext_dir = os.environ.get("GLASSFOLIO_EXTENSIONS")
    bundled = (Path(ext_dir) if ext_dir else resource_root() / "extensions") / "ducklake.duckdb_extension"
    if bundled.exists():  # the desktop app ships it: no download on first launch
        con.execute(f"LOAD {_sql_str(str(bundled))}")
    else:
        con.execute("INSTALL ducklake; LOAD ducklake")
    meta = _sql_str(f"ducklake:{home / 'catalog.duckdb'}")
    data = _sql_str(str(home / "data") + "/")
    con.execute(
        f"ATTACH {meta} AS {CATALOG} (DATA_PATH {data}, ENCRYPTED, "
        f"METADATA_PARAMETERS MAP {{'ENCRYPTION_KEY': {_sql_str(key)}}})"
    )
    con.execute(f"USE {CATALOG}")
    schema = resources.files("glassfolio").joinpath("sql", "schema.sql").read_text()
    con.execute(schema)
    return Lake(con)


def insert_rows(con: duckdb.DuckDBPyConnection, table: str, rows: Sequence[tuple]) -> int:
    """Bulk-append rows (in table column order) with one columnar statement.

    Decimals travel as strings so the table's DECIMAL columns parse them exactly.
    """
    if not rows:
        return 0
    columns = list(zip(*rows))
    params = {f"c{i}": [str(v) if isinstance(v, Decimal) else v for v in col]
              for i, col in enumerate(columns)}
    select = ", ".join(f"unnest($c{i})" for i in range(len(columns)))
    con.execute(f"INSERT INTO {table} SELECT {select}", params)
    return len(rows)


def current_snapshot(lake: Lake) -> int:
    return lake.con.execute(f"SELECT id FROM {CATALOG}.current_snapshot()").fetchone()[0]


def run_write(
    lake: Lake, meta: OpMeta, work: Callable[[duckdb.DuckDBPyConnection], tuple[T, RowCounts]]
) -> tuple[T, str]:
    """Run `work` in one transaction and record it in ops_log.

    `meta.params` must not contain amounts or quantities.
    """
    if meta.actor not in ACTORS:
        raise ValueError(f"unknown actor: {meta.actor}")
    op_id = new_id("op")
    con = lake.con
    before = current_snapshot(lake)
    con.execute("BEGIN")
    try:
        result, counts = work(con)
        con.execute(
            "INSERT INTO ops_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [op_id, utc_now(), meta.actor, meta.tool, json.dumps(dict(meta.params)),
             meta.description, counts.inserted, counts.updated, counts.deleted, before],
        )
        con.execute(
            f"CALL {CATALOG}.set_commit_message(?, ?, extra_info => ?)",
            [meta.actor, meta.tool, op_id],
        )
        con.execute("COMMIT")
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except duckdb.Error:
            pass  # e.g. COMMIT itself failed; keep the original error
        raise
    return result, op_id


@dataclass(frozen=True)
class OpEntry:
    op_id: str
    ts: datetime
    actor: str
    tool: str
    params: str
    description: str
    rows_inserted: int
    rows_updated: int
    rows_deleted: int
    snapshot_before: int
    snapshot_after: int | None


def list_ops(lake: Lake, limit: int = 50) -> tuple[OpEntry, ...]:
    rows = lake.con.execute(
        f"""
        SELECT o.*, s.snapshot_id
        FROM ops_log o
        LEFT JOIN (SELECT snapshot_id, commit_extra_info FROM {CATALOG}.snapshots()) s
          ON s.commit_extra_info = o.op_id
        ORDER BY o.ts DESC LIMIT ?
        """,
        [limit],
    ).fetchall()
    return tuple(OpEntry(*row) for row in rows)


def restore(lake: Lake, op_id: str, actor: str = "user") -> str:
    """Restore every business table to its state just before `op_id`.

    Later operations are rolled back too. ops_log itself is never rewritten,
    and the restore is logged as its own operation.
    """
    row = lake.con.execute(
        "SELECT snapshot_before FROM ops_log WHERE op_id = ?", [op_id]
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown op_id: {op_id}")
    before = int(row[0])

    def work(con: duckdb.DuckDBPyConnection) -> tuple[None, RowCounts]:
        counts = RowCounts()
        for table in BUSINESS_TABLES:
            # Stage first: inside a transaction, a time-travel read issued after
            # DELETE sees that delete and returns nothing.
            con.execute(f"CREATE OR REPLACE TEMP TABLE _restore AS "
                        f"SELECT * FROM {table} AT (VERSION => {before})")
            deleted = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            con.execute(f"DELETE FROM {table}")
            con.execute(f"INSERT INTO {table} SELECT * FROM temp.main._restore")
            inserted = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            con.execute("DROP TABLE temp.main._restore")
            counts = counts + RowCounts(inserted=inserted, deleted=deleted)
        return None, counts

    meta = OpMeta(actor, "restore", {"op_id": op_id}, f"restore to before {op_id}")
    return run_write(lake, meta, work)[1]
