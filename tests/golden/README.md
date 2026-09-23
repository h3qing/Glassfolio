# Golden dataset (synthetic)

Entirely fictional numbers. Never put real financial data in this repo.

Valuation date D = 2026-09-18. Hand-computed expected values live in
`tests/test_golden.py`.

| File | What it exercises |
| --- | --- |
| `broker_alice_taxable.csv` | title line, `$`/`,` formatting, cash row, total row; QQQ export price (50.10) differs from close (50.00) |
| `broker_alice_roth.csv` | snapshot dated 2026-09-12, before the MSFT 2:1 split on 2026-09-16 |
| `broker_profile.json` | column mapping for the synthetic "Generic Broker" |
| `etf_qqq_*.csv` | share baskets; three versions (08-31, 09-17, 09-25) to test as-of selection; USD cash line |
| `etf_vti.csv` | weights only (no shares) → weight fallback |
| `etf_gfof_ishares.csv` | iShares-style layout; fund of funds holding QQQ + VTI |
| `prices.csv` | closes; MSFT pre-split 800 → post-split 400 |
| `corporate_actions.csv` | MSFT 2:1 split |

GCIT is a collective trust with no holdings; it is proxied to VTI.
