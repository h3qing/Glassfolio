import json
import threading
import duckdb
import pytest

from conftest import GOLDEN, TEST_KEY, D
from glassfolio.broker_import import commit_statement, preview_statement
from glassfolio.exposure import company_exposure
from glassfolio.keys import DbKeyError
from glassfolio.lake import list_ops, open_lake, restore
from glassfolio.registry import add_owner


def test_wrong_key_cannot_open(tmp_path):
    lake = open_lake(tmp_path / "h", TEST_KEY)
    add_owner(lake, "alice")
    lake.con.close()
    with pytest.raises(duckdb.Error):
        open_lake(tmp_path / "h", "f" * 64)


def test_catalog_file_is_not_plaintext(tmp_path):
    lake = open_lake(tmp_path / "h", TEST_KEY)
    add_owner(lake, "unmistakable-nickname")
    lake.con.close()
    assert b"unmistakable-nickname" not in (tmp_path / "h" / "catalog.duckdb").read_bytes()


def test_malformed_key_rejected(tmp_path):
    with pytest.raises(DbKeyError):
        open_lake(tmp_path / "h", "short")


def test_every_write_is_logged_with_snapshots(golden):
    lake, _ = golden
    ops = list_ops(lake, limit=100)
    assert {o.tool for o in ops} >= {"add_owner", "add_account", "import_statement",
                                     "import_etf_holdings", "import_prices", "set_proxy"}
    for op in ops:
        assert op.snapshot_after is not None and op.snapshot_after > op.snapshot_before


def test_ops_log_params_hold_no_amounts(golden):
    lake, _ = golden
    allowed = {"owner_id", "account_id", "profile_id", "broker", "file_hash", "as_of", "etf",
               "source", "tickers", "rows", "security_id", "proxy_security_id"}
    for op in list_ops(lake, limit=100):
        assert set(json.loads(op.params)) <= allowed, op.tool


def test_duplicate_statement_is_refused(golden):
    lake, profile = golden
    preview = preview_statement(lake, GOLDEN / "broker_alice_taxable.csv", "Alice Taxable", profile, D)
    assert preview.duplicate
    with pytest.raises(ValueError, match="already imported"):
        commit_statement(lake, preview)


def test_restore_rolls_back_an_import(golden, tmp_path):
    lake, profile = golden
    (before,) = company_exposure(lake, D, "NVDA")
    text = (GOLDEN / "broker_alice_taxable.csv").read_text()
    corrected = tmp_path / "corrected.csv"
    corrected.write_text(text.replace('"10","$100.00","$1,000.00"', '"30","$100.00","$3,000.00"'))
    op = commit_statement(lake, preview_statement(lake, corrected, "Alice Taxable", profile, D))
    (after,) = company_exposure(lake, D, "NVDA")
    assert after.direct_value == pytest.approx(3000)
    restore_op = restore(lake, op)
    (restored,) = company_exposure(lake, D, "NVDA")
    assert restored.total == pytest.approx(before.total)
    assert any(o.op_id == restore_op and o.tool == "restore" for o in list_ops(lake))


def _in_two_threads(task, args) -> None:
    start = threading.Barrier(len(args))

    def run(arg):
        start.wait()
        task(arg)

    threads = [threading.Thread(target=run, args=(a,)) for a in args]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_threads_never_get_each_others_rows(lake):
    """DuckDB keeps a pending result on the connection, so a shared one mixes them up."""
    add_owner(lake, "alice")
    add_owner(lake, "bob")
    wrong = []

    def ask(name):
        for _ in range(1000):
            try:
                row = lake.con.execute("SELECT nickname FROM owners WHERE nickname = ?", [name]).fetchone()
            except duckdb.Error as exc:
                row = type(exc).__name__
            if row != (name,):
                wrong.append((name, row))

    _in_two_threads(ask, ("alice", "bob"))
    assert wrong == []


def test_concurrent_writes_keep_ops_log_snapshots_exact(lake):
    """Each op's snapshot_before must be the snapshot its commit follows, or restore undoes too much."""
    failed = []

    def write(prefix):
        for i in range(15):
            try:
                add_owner(lake, f"{prefix}{i}")
            except duckdb.Error as exc:
                failed.append(type(exc).__name__)

    _in_two_threads(write, ("a", "b"))
    ops = list_ops(lake, limit=100)
    assert failed == []
    assert len(ops) == 30
    assert all(o.snapshot_after == o.snapshot_before + 1 for o in ops)
