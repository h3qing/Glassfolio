"""Spec §4.3: infer cash flows from snapshot differences; ask when unclear."""

from datetime import date

import pytest

from conftest import GOLDEN, T1, load_statement
from glassfolio.flows import list_cash_flows, list_inbox, resolve_flow
from glassfolio.registry import add_account, find_account


def account_id(lake, nickname):
    return find_account(lake.con, nickname).account_id


def test_first_statement_creates_no_flow(lake):
    from glassfolio.registry import add_owner, add_profile
    import json
    add_owner(lake, "alice")
    add_account(lake, "A", "alice", "G", "taxable")
    profile = add_profile(lake, "G", json.loads((GOLDEN / "broker_profile.json").read_text()))
    load_statement(lake, "broker_alice_taxable.csv", "A", profile, date(2026, 9, 18))
    assert list_inbox(lake) == () and list_cash_flows(lake) == ()


def test_unexplained_deposit_goes_to_inbox(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    assert item.type == "unexplained_flow" and item.status == "open"
    assert item.payload["amount"] == pytest.approx(3000)  # 11,350 − 8,350
    assert item.payload["account"] == "Alice Taxable"
    assert item.payload["end"] == T1.isoformat()


def test_resolving_as_deposit_records_confirmed_flow(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    resolve_flow(lake, item.item_id, "deposit")
    assert list_inbox(lake) == ()
    (flow,) = list_cash_flows(lake)
    assert (flow.type, flow.source, flow.date) == ("deposit", "user_confirmed", T1)
    assert flow.amount == pytest.approx(3000)
    with pytest.raises(ValueError, match="already resolved"):
        resolve_flow(lake, item.item_id, "deposit")


def test_remembered_rule_classifies_next_time(golden, tmp_path):
    lake, profile = golden
    (item,) = list_inbox(lake)
    resolve_flow(lake, item.item_id, "deposit", remember=True)
    text = (GOLDEN / "broker_alice_taxable_2026-09-30.csv").read_text()
    later = tmp_path / "oct.csv"
    later.write_text(text.replace("$3,780.00", "$5,780.00").replace("09/30", "10/15"))
    from glassfolio.broker_import import commit_statement, preview_statement
    commit_statement(lake, preview_statement(lake, later, "Alice Taxable", profile, date(2026, 10, 15)))
    assert list_inbox(lake) == ()
    auto = [f for f in list_cash_flows(lake) if f.source == "rule"]
    assert len(auto) == 1 and auto[0].amount == pytest.approx(2000) and auto[0].type == "deposit"


def test_small_difference_is_recorded_without_asking(golden, tmp_path):
    lake, profile = golden
    (item,) = list_inbox(lake)
    resolve_flow(lake, item.item_id, "deposit")
    text = (GOLDEN / "broker_alice_taxable_2026-09-30.csv").read_text()
    later = tmp_path / "oct.csv"
    later.write_text(text.replace("$3,780.00", "$3,880.00").replace("09/30", "10/15"))
    from glassfolio.broker_import import commit_statement, preview_statement
    commit_statement(lake, preview_statement(lake, later, "Alice Taxable", profile, date(2026, 10, 15)))
    assert list_inbox(lake) == ()
    inferred = [f for f in list_cash_flows(lake) if f.source == "inferred"]
    assert len(inferred) == 1 and inferred[0].amount == pytest.approx(100)


ROTH_0930 = """"Positions for account Alice Roth as of 09/30/2026"
"Symbol","Description","Quantity","Price","Market Value","Cost Basis"
"MSFT","MICROSOFT CORP","10","$410.00","$4,100.00","$3,000.00"
"VTI","VANGUARD TOTAL STOCK MKT ETF","80","$25.00","$2,000.00","$1,800.00"
"GCIT","GLASS COLLECTIVE TRUST","100","$10.40","$1,040.00","$1,000.00"
"Cash & Cash Investments","--","--","--","$0.00","--"
"""


def test_transfer_pairs_two_accounts(golden, tmp_path):
    lake, profile = golden
    (deposit_item,) = list_inbox(lake)
    # Roth 09-12 → 09-30: 120 VTI (3,000 at 25) left the account; MSFT split 2:1 is not a flow.
    out = tmp_path / "roth.csv"
    out.write_text(ROTH_0930)
    from glassfolio.broker_import import commit_statement, preview_statement
    commit_statement(lake, preview_statement(lake, out, "Alice Roth", profile, T1))
    items = {i.payload["account"]: i for i in list_inbox(lake)}
    roth_item = items["Alice Roth"]
    assert roth_item.payload["amount"] == pytest.approx(-3000)
    assert deposit_item.item_id in [c["item_id"] for c in roth_item.pair_candidates]
    resolve_flow(lake, roth_item.item_id, "transfer", paired_item_id=deposit_item.item_id)
    assert list_inbox(lake) == ()
    flows = list_cash_flows(lake)
    assert {f.type for f in flows} == {"transfer"} and len(flows) == 2
    assert sum(f.amount for f in flows) == pytest.approx(0)
    assert {f.paired_flow_id for f in flows} == {f.flow_id for f in flows}
