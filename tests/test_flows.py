"""Spec §4.3: cash flows inferred from statement differences; ask when unclear.

Flows are derived from the statements on every read, so corrections, late
dividends and out-of-order imports can't leave stale flows behind. Only answers
and rules are stored.
"""

import json
from datetime import date

import pytest

from conftest import GOLDEN, T1, load_statement
from glassfolio.broker_import import commit_statement, preview_statement
from glassfolio.flows import list_cash_flows, list_inbox, resolve_flow
from glassfolio.registry import add_account, add_owner, add_profile

HEAD = '"t"\n"Symbol","Description","Quantity","Price","Market Value","Cost Basis"\n'


def stmt(lake, tmp_path, account, profile, as_of, rows, cash):
    path = tmp_path / f"{account}-{as_of}-{cash}-{len(rows)}.csv"
    body = "".join(f'"{s}","{s} INC","{q}","${p}","${q * p}","--"\n' for s, q, p in rows)
    path.write_text(HEAD + body + f'"Cash & Cash Investments","--","--","--","${cash}","--"\n')
    return commit_statement(lake, preview_statement(lake, path, account, profile, as_of))


@pytest.fixture
def blank(lake):
    add_owner(lake, "bob")
    add_account(lake, "B", "bob", "G", "taxable")
    profile = add_profile(lake, "G", json.loads((GOLDEN / "broker_profile.json").read_text()))
    return lake, profile


def amounts(lake):
    return sorted(round(f.amount, 2) for f in list_cash_flows(lake))


def open_amounts(lake):
    return sorted(round(i.payload["amount"], 2) for i in list_inbox(lake) if i.type == "unexplained_flow")


def test_first_statement_creates_no_flow(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [], 10000)
    assert list_inbox(lake) == () and list_cash_flows(lake) == ()


def test_unexplained_deposit_goes_to_inbox(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    assert item.type == "unexplained_flow"
    assert item.payload["amount"] == pytest.approx(3000)  # 11,350 − 8,350
    assert item.payload["account"] == "Alice Taxable" and item.payload["end"] == T1.isoformat()


def test_resolving_as_deposit_records_confirmed_flow(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    resolve_flow(lake, item.item_id, "deposit")
    assert list_inbox(lake) == ()
    (flow,) = list_cash_flows(lake)
    assert (flow.type, flow.source, flow.date) == ("deposit", "user_confirmed", T1)
    assert flow.amount == pytest.approx(3000)


def test_answer_can_be_changed_later(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    resolve_flow(lake, item.item_id, "deposit")
    resolve_flow(lake, item.item_id, "dividend")
    (flow,) = list_cash_flows(lake)
    assert flow.type == "dividend"


def test_remembered_rule_classifies_next_time(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [], 10000)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 13000)
    (item,) = list_inbox(lake)
    resolve_flow(lake, item.item_id, "deposit", remember=True)
    stmt(lake, tmp_path, "B", profile, date(2026, 3, 31), [], 15000)
    assert list_inbox(lake) == ()
    assert [(f.source, f.type, f.amount) for f in list_cash_flows(lake)] == [
        ("user_confirmed", "deposit", 3000), ("rule", "deposit", 2000)]


def test_small_difference_is_recorded_without_asking(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [], 10000)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 10100)
    assert list_inbox(lake) == ()
    (flow,) = list_cash_flows(lake)
    assert (flow.source, flow.amount) == ("inferred", 100)


def test_out_of_order_import_does_not_double_count(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [], 11000)
    stmt(lake, tmp_path, "B", profile, date(2026, 3, 31), [], 21000)
    assert open_amounts(lake) == [10000]
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 16000)  # imported late
    assert open_amounts(lake) == [5000, 5000]


def test_corrected_statement_for_same_date_replaces_the_old_one(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [], 1000)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 1300)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 1301)  # correction
    assert amounts(lake) == [301]


def test_sold_security_without_a_later_price_is_a_data_gap(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [("XYZ", 100, 100)], 0)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 15000)  # sold at 150
    (item,) = list_inbox(lake)
    assert item.type == "data_gap" and list_cash_flows(lake) == ()


def test_sold_security_with_a_later_price_is_explained(blank, tmp_path):
    from glassfolio.market_import import import_prices
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [("XYZ", 100, 100)], 0)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 15000)
    prices = tmp_path / "p.csv"
    prices.write_text("date,ticker,close\n2026-02-27,XYZ,150\n")
    import_prices(lake, prices)
    assert list_inbox(lake) == () and list_cash_flows(lake) == ()


def test_dividends_arriving_later_are_taken_into_account(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [("XYZ", 100, 100)], 0)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [("XYZ", 100, 100)], 100)
    assert amounts(lake) == [100]  # looks like a small deposit…
    lake.con.execute("INSERT INTO prices SELECT security_id, DATE '2026-02-10', 100, 1.0, 'tiingo' "
                     "FROM securities WHERE ticker = 'XYZ'")
    lake.con.execute("INSERT INTO prices SELECT security_id, DATE '2026-02-10', 100, 1.0, 'tiingo' "
                     "FROM securities WHERE ticker = 'XYZ'")  # fetched twice
    assert amounts(lake) == []  # …until the dividend is known (counted once)


def test_non_flow_questions_only_accept_acknowledgement(blank, tmp_path):
    lake, profile = blank
    stmt(lake, tmp_path, "B", profile, date(2026, 1, 31), [("XYZ", 100, 100)], 0)
    stmt(lake, tmp_path, "B", profile, date(2026, 2, 28), [], 15000)
    (item,) = list_inbox(lake)
    with pytest.raises(ValueError, match="can only be marked as checked"):
        resolve_flow(lake, item.item_id, "deposit")
    resolve_flow(lake, item.item_id, "not_a_flow")
    assert list_inbox(lake) == ()


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
    # Roth 09-12 → 09-30: 120 VTI (3,000 at 25) left; the MSFT 2:1 split is not a flow.
    out = tmp_path / "roth.csv"
    out.write_text(ROTH_0930)
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
