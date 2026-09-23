"""Drive the CLI end to end on the golden data, as a user would."""

import pytest

from conftest import GOLDEN, TEST_KEY
from glassfolio.cli import main


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GLASSFOLIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GLASSFOLIO_DB_KEY", TEST_KEY)

    def run(*argv):
        main([str(a) for a in argv])
        return capsys.readouterr().out

    return run


def test_mvp_flow(cli, tmp_path):
    cli("owner", "add", "alice")
    cli("account", "add", "Alice Taxable", "--owner", "alice", "--broker", "Generic", "--type", "taxable")
    profile = cli("profile", "add", "Generic", GOLDEN / "broker_profile.json").strip()
    cli("import", "etf", GOLDEN / "etf_qqq_2026-09-17.csv", "--etf", "QQQ", "--as-of", "2026-09-17",
        "--shares-outstanding", "1000000", "-y")
    cli("import", "etf", GOLDEN / "etf_vti.csv", "--etf", "VTI", "--as-of", "2026-09-17", "-y")
    cli("import", "etf", GOLDEN / "etf_gfof_ishares.csv", "--etf", "GFOF", "--format", "ishares", "-y")
    preview = cli("import", "statement", GOLDEN / "broker_alice_taxable.csv", "--account",
                  "Alice Taxable", "--profile", profile, "--as-of", "2026-09-18", "-y")
    assert "INVESCO QQQ TRUST" in preview and "8,010.00" in preview

    # GCIT is only held in the Roth account, which this flow does not import.
    lines = (GOLDEN / "prices.csv").read_text().splitlines()
    prices = tmp_path / "prices.csv"
    prices.write_text("\n".join(l for l in lines if "GCIT" not in l) + "\n")
    cli("import", "prices", prices)

    out = cli("exposure", "--ticker", "NVDA", "--as-of", "2026-09-18")
    assert "1,000.00" in out and "2,300.00" in out and "3,300.00" in out

    out = cli("check", "--account", "Alice Taxable", "--as-of", "2026-09-18",
              "--reported-total", "8010", "--reported-cost", "5500")
    assert "✓ account_total" in out and "✓ cost_total" in out and "✓ conservation" in out

    assert "import_statement" in cli("ops")


def test_duplicate_import_exits(cli):
    cli("owner", "add", "alice")
    cli("account", "add", "A", "--owner", "alice", "--broker", "G", "--type", "taxable")
    profile = cli("profile", "add", "G", GOLDEN / "broker_profile.json").strip()
    args = ("import", "statement", GOLDEN / "broker_alice_taxable.csv", "--account", "A",
            "--profile", profile, "--as-of", "2026-09-18", "-y")
    cli(*args)
    with pytest.raises(SystemExit, match="already imported"):
        cli(*args)


def test_delete_source_removes_plaintext_after_import(cli, tmp_path):
    cli("owner", "add", "alice")
    cli("account", "add", "A", "--owner", "alice", "--broker", "G", "--type", "taxable")
    profile = cli("profile", "add", "G", GOLDEN / "broker_profile.json").strip()
    copy = tmp_path / "statement.csv"
    copy.write_bytes((GOLDEN / "broker_alice_taxable.csv").read_bytes())
    cli("import", "statement", copy, "--account", "A", "--profile", profile,
        "--as-of", "2026-09-18", "--delete-source", "-y")
    assert not copy.exists()
