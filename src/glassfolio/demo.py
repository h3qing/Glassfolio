"""Load the synthetic golden portfolio (tests/golden) into a lake.

Used by the tests, the demo and the assistant evaluation — never real data.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from glassfolio.broker_import import commit_statement, preview_statement
from glassfolio.etf_import import commit_etf_holdings, preview_etf_holdings
from glassfolio.lake import Lake
from glassfolio.market_import import import_corporate_actions, import_prices
from glassfolio.registry import add_account, add_owner, add_profile, set_proxy

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "golden"
D = date(2026, 9, 18)
T1 = date(2026, 9, 30)


def load_etf(lake: Lake, name: str, ticker: str, fmt: str = "generic", as_of=None, outstanding=None,
             golden: Path = GOLDEN) -> str:
    return commit_etf_holdings(lake, preview_etf_holdings(lake, golden / name, ticker, fmt, as_of, outstanding))


def load_statement(lake: Lake, name: str, account: str, profile_id: str, as_of: date,
                   golden: Path = GOLDEN) -> str:
    return commit_statement(lake, preview_statement(lake, golden / name, account, profile_id, as_of))


def build_golden(lake: Lake, golden: Path = GOLDEN) -> str:
    add_owner(lake, "alice")
    add_account(lake, "Alice Taxable", "alice", "Generic Broker", "taxable")
    add_account(lake, "Alice Roth", "alice", "Generic Broker", "roth_ira")
    profile = add_profile(lake, "Generic Broker", json.loads((golden / "broker_profile.json").read_text()))
    million = Decimal(1_000_000)
    for day in ("2026-08-31", "2026-09-17", "2026-09-25"):
        load_etf(lake, f"etf_qqq_{day}.csv", "QQQ", as_of=date.fromisoformat(day), outstanding=million, golden=golden)
    load_etf(lake, "etf_vti.csv", "VTI", as_of=date(2026, 9, 17), golden=golden)
    load_etf(lake, "etf_gfof_ishares.csv", "GFOF", fmt="ishares", golden=golden)
    load_statement(lake, "broker_alice_taxable.csv", "Alice Taxable", profile, D, golden)
    load_statement(lake, "broker_alice_roth.csv", "Alice Roth", profile, date(2026, 9, 12), golden)
    load_statement(lake, "broker_alice_taxable_2026-09-30.csv", "Alice Taxable", profile, T1, golden)
    import_prices(lake, golden / "prices.csv")
    import_corporate_actions(lake, golden / "corporate_actions.csv")
    set_proxy(lake, "GCIT", "VTI")
    return profile
