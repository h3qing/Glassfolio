import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from glassfolio.broker_import import commit_statement, preview_statement
from glassfolio.etf_import import commit_etf_holdings, preview_etf_holdings
from glassfolio.lake import open_lake
from glassfolio.market_import import import_corporate_actions, import_prices
from glassfolio.registry import add_account, add_owner, add_profile, set_proxy

GOLDEN = Path(__file__).parent / "golden"
TEST_KEY = "0" * 63 + "1"  # test-only key; real keys come from the Keychain
D = date(2026, 9, 18)


@pytest.fixture
def lake(tmp_path):
    return open_lake(tmp_path / "home", TEST_KEY)


def load_etf(lake, name, ticker, fmt="generic", as_of=None, outstanding=None):
    preview = preview_etf_holdings(lake, GOLDEN / name, ticker, fmt, as_of, outstanding)
    return commit_etf_holdings(lake, preview)


def load_statement(lake, name, account, profile_id, as_of):
    return commit_statement(lake, preview_statement(lake, GOLDEN / name, account, profile_id, as_of))


def build_golden(lake):
    add_owner(lake, "alice")
    add_account(lake, "Alice Taxable", "alice", "Generic Broker", "taxable")
    add_account(lake, "Alice Roth", "alice", "Generic Broker", "roth_ira")
    profile = add_profile(lake, "Generic Broker", json.loads((GOLDEN / "broker_profile.json").read_text()))
    million = Decimal(1_000_000)
    load_etf(lake, "etf_qqq_2026-08-31.csv", "QQQ", as_of=date(2026, 8, 31), outstanding=million)
    load_etf(lake, "etf_qqq_2026-09-17.csv", "QQQ", as_of=date(2026, 9, 17), outstanding=million)
    load_etf(lake, "etf_qqq_2026-09-25.csv", "QQQ", as_of=date(2026, 9, 25), outstanding=million)
    load_etf(lake, "etf_vti.csv", "VTI", as_of=date(2026, 9, 17))
    load_etf(lake, "etf_gfof_ishares.csv", "GFOF", fmt="ishares")
    load_statement(lake, "broker_alice_taxable.csv", "Alice Taxable", profile, D)
    load_statement(lake, "broker_alice_roth.csv", "Alice Roth", profile, date(2026, 9, 12))
    import_prices(lake, GOLDEN / "prices.csv")
    import_corporate_actions(lake, GOLDEN / "corporate_actions.csv")
    set_proxy(lake, "GCIT", "VTI")
    return profile


@pytest.fixture
def golden(lake):
    profile = build_golden(lake)
    return lake, profile
