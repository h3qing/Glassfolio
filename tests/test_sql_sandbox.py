"""query_readonly: a sandbox with copies of allow-listed tables, nothing else."""

import pytest

from conftest import D
from glassfolio.assistant.sql_sandbox import SandboxError, TABLES, query_readonly


def test_can_answer_questions_with_sql(golden):
    lake, _ = golden
    rows = query_readonly(lake, "SELECT count(*) AS n FROM accounts", D)
    assert rows["columns"] == ["n"] and rows["rows"] == [[2]]
    looked = query_readonly(lake, "SELECT round(sum(value)) FROM exposure WHERE kind = 'position'", D)
    assert looked["rows"] == [[18000.0]]


@pytest.mark.parametrize("sql", [
    "SELECT getenv('GLASSFOLIO_DB_KEY')",
    "SELECT * FROM read_text('/etc/hosts')",
    "SELECT * FROM read_csv('/etc/hosts')",
    "SELECT * FROM query('SELECT 1')",
    "COPY accounts TO '/tmp/x.csv'",
    "SELECT 1; DROP TABLE accounts",
    "ATTACH '/tmp/x.db'",
    "SELECT raw_content FROM import_files",
    "SELECT * FROM ops_log",
    "INSTALL httpfs",
])
def test_everything_else_is_refused(golden, sql):
    lake, _ = golden
    with pytest.raises(SandboxError):
        query_readonly(lake, sql, D)


def test_rows_are_capped(golden):
    lake, _ = golden
    out = query_readonly(lake, "SELECT a.account_id FROM accounts a, securities s, securities t, securities u", D)
    assert len(out["rows"]) <= 200 and out["truncated"]


def test_the_real_lake_is_untouched(golden):
    lake, _ = golden
    with pytest.raises(SandboxError):
        query_readonly(lake, "DELETE FROM accounts", D)
    assert lake.con.execute("SELECT count(*) FROM accounts").fetchone()[0] == 2


def test_allowlist_excludes_sensitive_tables():
    assert "import_files" not in TABLES or "raw_content" not in TABLES["import_files"]
    assert "ops_log" not in TABLES


def test_runaway_queries_are_stopped_and_the_app_survives(golden):
    lake, _ = golden
    runaway = ("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 10000000000) "
               "SELECT count(*) FROM r")
    with pytest.raises(SandboxError):
        query_readonly(lake, runaway, D)
    assert query_readonly(lake, "SELECT count(*) FROM accounts", D)["rows"] == [[2]]
