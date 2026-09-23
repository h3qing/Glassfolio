import json
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
