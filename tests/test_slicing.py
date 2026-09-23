"""Phase 2: slice exposure by person, account type, broker and account."""

import pytest

from conftest import D
from glassfolio.broker_import import commit_statement, preview_statement
from glassfolio.exposure import Slice, company_exposure, portfolio_summary
from glassfolio.registry import add_account, add_owner


@pytest.fixture
def family(golden, tmp_path):
    lake, profile = golden
    add_owner(lake, "bob")
    add_account(lake, "Bob 401k", "bob", "Fidelity", "401k")
    path = tmp_path / "bob.csv"
    path.write_text('"title"\n"Symbol","Description","Quantity","Price","Market Value","Cost Basis"\n'
                    '"NVDA","NVIDIA CORP","5","$100.00","$500.00","$400.00"\n'
                    '"Cash & Cash Investments","--","--","--","$100.00","--"\n')
    commit_statement(lake, preview_statement(lake, path, "Bob 401k", profile, D))
    return lake


def nvda(lake, slice_=Slice(), group_by=None):
    return {r.group: r.total for r in company_exposure(lake, D, "NVDA", group_by, slice_)}


def test_slice_by_owner(family):
    assert nvda(family, Slice(owner="alice")) == pytest.approx({None: 4500})
    assert nvda(family, Slice(owner="bob")) == pytest.approx({None: 500})
    assert nvda(family) == pytest.approx({None: 5000})


def test_slice_by_account_type_and_broker(family):
    assert nvda(family, Slice(account_type="roth_ira")) == pytest.approx({None: 1200})
    assert nvda(family, Slice(broker="Fidelity")) == pytest.approx({None: 500})
    assert nvda(family, Slice(account="Alice Taxable")) == pytest.approx({None: 3300})


def test_group_by_owner_and_account_type(family):
    assert nvda(family, group_by="owner") == pytest.approx({"alice": 4500, "bob": 500})
    assert nvda(family, group_by="account_type") == pytest.approx(
        {"taxable": 3300, "roth_ira": 1200, "401k": 500})


def test_portfolio_summary_respects_slice(family):
    whole = portfolio_summary(family, D)
    assert whole.total_value == pytest.approx(18600)
    assert whole.cash_value == pytest.approx(1320)
    bob = portfolio_summary(family, D, Slice(owner="bob"))
    assert bob.total_value == pytest.approx(600)
    assert bob.approx_value == 0 and bob.missing_prices == 0
    alice = portfolio_summary(family, D, Slice(owner="alice"))
    assert alice.approx_value > 0


def test_unknown_owner_slice_is_empty(family):
    assert company_exposure(family, D, slice_=Slice(owner="nobody")) == ()
