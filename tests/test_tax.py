"""Spec §2.3: after-tax values by account type, person and assumptions.

Default profile (California): federal LTCG 15%, ordinary 24%, CA 9.3%, no NIIT
→ LTCG 24.3%, STCG 33.3%, withdrawals 33.3%. Without lots: all short-term.
Taxable on 09-18 at closes: NVDA 1,000 (cost 600), QQQ 5,000 (4,000), GFOF 1,000
(900), cash 1,000 → gains 1,500.
"""

from datetime import date

import pytest

from conftest import D, GOLDEN
from glassfolio.exposure import Slice
from glassfolio.tax import (Scenario, company_after_tax, portfolio_after_tax, position_taxes)
from glassfolio.tax_profiles import (TaxProfile, add_tax_profile, assign_tax_profile, effective_rates,
                                     import_lots, set_account_treatment)
from glassfolio.registry import add_account

CA = TaxProfile(name="CA", federal_ltcg_rate=0.15, federal_ordinary_rate=0.24, niit=False,
                state="CA", state_rate=0.093)


def by_ticker(rows):
    return {r.ticker: r for r in rows}


def test_effective_rates():
    r = effective_rates(CA)
    assert (r.ltcg, r.stcg, r.withdrawal) == pytest.approx((0.243, 0.333, 0.333))
    niit = effective_rates(TaxProfile(**{**vars(CA), "niit": True}))
    assert niit.ltcg == pytest.approx(0.281) and niit.withdrawal == pytest.approx(0.333)


def test_default_profile_is_california_short_term(golden):
    lake, _ = golden
    total = portfolio_after_tax(lake, D, Slice(account="Alice Taxable"))
    assert total.pre_tax == pytest.approx(8000)
    assert total.tax == pytest.approx(1500 * 0.333)
    assert total.after_tax == pytest.approx(8000 - 499.5)


def test_roth_is_untaxed_and_traditional_pays_on_withdrawal(golden, tmp_path):
    lake, profile = golden
    assert portfolio_after_tax(lake, D, Slice(account="Alice Roth")).tax == pytest.approx(0)
    add_account(lake, "Alice 401k", "alice", "Generic Broker", "401k")
    text = (GOLDEN / "broker_alice_taxable.csv").read_text().replace("Alice Taxable", "Alice 401k")
    path = tmp_path / "k.csv"
    path.write_text(text)
    from glassfolio.broker_import import commit_statement, preview_statement
    commit_statement(lake, preview_statement(lake, path, "Alice 401k", profile, D))
    k = portfolio_after_tax(lake, D, Slice(account="Alice 401k"))
    assert k.after_tax == pytest.approx(8000 * (1 - 0.333))


def test_hsa_and_529_are_tax_exempt():
    from glassfolio.tax_profiles import default_treatment
    assert default_treatment("hsa") == default_treatment("529") == default_treatment("roth_ira") == "exempt"
    assert default_treatment("401k") == default_treatment("traditional_ira") == "deferred"
    assert default_treatment("taxable") == "taxable"


def test_long_term_assumption_and_explicit_profile(golden):
    lake, _ = golden
    pid = add_tax_profile(lake, TaxProfile(**{**vars(CA), "name": "LT", "no_lot_assumption": "long_term"}))
    assign_tax_profile(lake, owner="alice", profile_id=pid)
    assert portfolio_after_tax(lake, D, Slice(account="Alice Taxable")).tax == pytest.approx(1500 * 0.243)


def test_lots_split_long_and_short_term(golden, tmp_path):
    lake, _ = golden
    lots = tmp_path / "lots.csv"
    lots.write_text("symbol,acquired_date,shares,cost\nNVDA,2024-01-02,6,300\nNVDA,2026-06-01,4,300\n")
    import_lots(lake, lots, "Alice Taxable", D)
    nvda = [p for p in position_taxes(lake, D) if p.ticker == "NVDA" and p.account == "Alice Taxable"][0]
    assert nvda.lt_gain == pytest.approx(300) and nvda.st_gain == pytest.approx(100)
    assert nvda.tax == pytest.approx(300 * 0.243 + 100 * 0.333)
    assert nvda.basis == "lots"


def test_lots_not_matching_the_position_fall_back_and_flag(golden, tmp_path):
    lake, _ = golden
    lots = tmp_path / "lots.csv"
    lots.write_text("symbol,acquired_date,shares,cost\nNVDA,2024-01-02,3,150\n")
    import_lots(lake, lots, "Alice Taxable", D)
    nvda = [p for p in position_taxes(lake, D) if p.ticker == "NVDA" and p.account == "Alice Taxable"][0]
    assert nvda.basis == "assumed (lots incomplete)" and nvda.st_gain == pytest.approx(400)


def test_account_can_be_marked_tax_exempt(golden):
    lake, _ = golden
    set_account_treatment(lake, "Alice Taxable", "exempt")
    assert portfolio_after_tax(lake, D, Slice(account="Alice Taxable")).tax == 0


def test_losses_count_unless_turned_off(golden):
    lake, _ = golden
    loss = Scenario(prices={"NVDA": 50.0})  # 10 × 50 = 500 < cost 600
    nvda = lambda: [p for p in position_taxes(lake, D, loss) if p.ticker == "NVDA"  # noqa: E731
                    and p.account == "Alice Taxable"][0]
    assert nvda().tax == pytest.approx(-100 * 0.333)
    pid = add_tax_profile(lake, TaxProfile(**{**vars(CA), "name": "no losses", "count_losses": False}))
    assign_tax_profile(lake, owner="alice", profile_id=pid)
    assert nvda().tax == 0


def test_company_after_tax_apportions_fund_tax(golden):
    lake, _ = golden
    nvda = by_ticker(company_after_tax(lake, D, Slice(account="Alice Taxable")))["NVDA"]
    # direct 1000 × (1 − 400×.333/1000); QQQ 2000 × (1 − 1000×.333/5000); GFOF 300 × (1 − 100×.333/1000)
    expected = 1000 * (1 - 0.1332) + 2000 * (1 - 0.0666) + 300 * (1 - 0.0333)
    assert nvda.total == pytest.approx(3300)
    assert nvda.after_tax == pytest.approx(expected)


def test_scenario_changes_rates_without_saving(golden):
    lake, _ = golden
    base = portfolio_after_tax(lake, D, Slice(account="Alice Taxable"))
    what_if = portfolio_after_tax(lake, D, Slice(account="Alice Taxable"), Scenario(state_rate=0.0))
    assert what_if.tax == pytest.approx(1500 * 0.24)
    assert portfolio_after_tax(lake, D, Slice(account="Alice Taxable")).tax == pytest.approx(base.tax)


def test_missing_cost_basis_is_flagged(golden):
    lake, _ = golden
    lake.con.execute("UPDATE positions SET cost_basis = NULL WHERE export_price = 20")  # GFOF
    total = portfolio_after_tax(lake, D, Slice(account="Alice Taxable"))
    assert total.missing_cost == 1


# ---- regressions from review -----------------------------------------------

def taxable_nvda(lake, as_of=D, scenario=Scenario()):
    return [p for p in position_taxes(lake, as_of, scenario)
            if p.ticker == "NVDA" and p.account == "Alice Taxable"][0]


def test_missing_price_is_not_a_negative_tax(golden):
    lake, _ = golden
    lake.con.execute("DELETE FROM prices WHERE security_id IN (SELECT security_id FROM securities WHERE ticker = 'NVDA')")
    nvda = taxable_nvda(lake)
    assert nvda.tax == 0 and nvda.basis == "no price"


def test_lots_are_split_adjusted(golden, tmp_path):
    lake, _ = golden
    lots = tmp_path / "lots.csv"
    lots.write_text("symbol,acquired_date,shares,cost\nNVDA,2020-01-02,10,600\n")  # as of 09-18
    import_lots(lake, lots, "Alice Taxable", D)
    split = tmp_path / "ca.csv"
    split.write_text("date,ticker,type,ratio_or_amount\n2026-09-20,NVDA,split,2\n")
    from glassfolio.market_import import import_corporate_actions
    import_corporate_actions(lake, split)
    nvda = taxable_nvda(lake, date(2026, 9, 21))  # statement 09-18: 10 sh → 20 post-split
    assert nvda.basis == "lots"


def test_lots_older_than_the_statement_are_ignored(golden, tmp_path):
    lake, _ = golden
    lots = tmp_path / "lots.csv"
    lots.write_text("symbol,acquired_date,shares,cost\nNVDA,2020-01-02,10,100\n")
    import_lots(lake, lots, "Alice Taxable", date(2026, 1, 1))  # before the 09-18 statement
    assert taxable_nvda(lake).basis == "assumed (lots out of date)"


@pytest.mark.parametrize("acquired,valued,long_term", [
    ("2023-03-01", "2024-03-01", False), ("2023-03-01", "2024-03-02", True),
    ("2024-02-28", "2025-02-28", False), ("2024-02-29", "2025-03-01", False),
    ("2024-02-29", "2025-03-02", True),
])
def test_holding_period_is_one_calendar_year(acquired, valued, long_term):
    from glassfolio.tax import is_long_term
    assert is_long_term(date.fromisoformat(acquired), date.fromisoformat(valued)) is long_term


def test_scenario_rates_are_validated():
    from glassfolio.tax_profiles import DEFAULT_PROFILE, validate_profile
    with pytest.raises(ValueError):
        validate_profile(Scenario(federal_ltcg_rate=5.0).apply(DEFAULT_PROFILE))
    with pytest.raises(ValueError):
        validate_profile(Scenario(state_rate=float("nan")).apply(DEFAULT_PROFILE))
    with pytest.raises(ValueError):
        validate_profile(Scenario(no_lot_assumption="sideways").apply(DEFAULT_PROFILE))


def test_account_override_can_be_cleared(golden):
    lake, _ = golden
    pid = add_tax_profile(lake, TaxProfile(**{**vars(CA), "name": "zero", "state_rate": 0.0}))
    assign_tax_profile(lake, pid, account="Alice Taxable")
    set_account_treatment(lake, "Alice Taxable", "taxable")
    assign_tax_profile(lake, None, account="Alice Taxable")
    assert portfolio_after_tax(lake, D, Slice(account="Alice Taxable")).tax == pytest.approx(1500 * 0.333)
