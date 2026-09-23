# Glassfolio

*See through your portfolio.*

Your accounts are spread across brokers, account types and family members, and the
same company (say NVDA) is held directly **and** inside several ETFs. Glassfolio
answers "how much NVDA do I actually own?" by looking through every fund down to the
companies, reconciling against your broker totals, and keeping all data on your
machine, encrypted.

> Status: phases 1–4, plus an early local web UI.
> - Encrypted storage, and an audited write log with restore.
> - Statement and ETF-holdings import.
> - Look-through exposure, sliced by person, account type and broker.
> - Reconciliation.
> - Daily prices and snapshots.
> - Cash flows inferred from statements, with questions when a difference is unclear.
> - Attribution of each change to price, your money or fund rebalancing.
> - TWR and MWR returns.
> - After-tax values by account type and person, with what-if rates.
>
> The local AI assistant and the Tauri desktop app come later
> ([roadmap](docs/SPEC.md#12-路线图)).

## Try it (synthetic data, 30 seconds)

Requires macOS and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/h3qing/Glassfolio && cd Glassfolio
./scripts/demo.sh
```

Add `--ui` to open the web UI on the same data (needs Node and pnpm to build it once):

```bash
./scripts/demo.sh --ui
```

This loads the [golden test portfolio](tests/golden/README.md) into a throwaway
database with a throwaway key, then prints the look-through exposure, the NVDA
breakdown by fund, a reconciliation, and the audit log. It never touches your
Keychain.

## Quick start (your own data)

```bash
uv sync
uv run glassfolio init                      # key → macOS Keychain; prints a recovery key
uv run glassfolio owner add me
uv run glassfolio account add "Schwab Taxable" --owner me --broker Schwab --type taxable
uv run glassfolio profile add Schwab mapping.json          # column mapping, see below
uv run glassfolio import etf QQQ_holdings.csv --etf QQQ --as-of 2026-09-17 --shares-outstanding 1000000
uv run glassfolio import etf IVV_holdings.csv --etf IVV --format ishares
uv run glassfolio import statement positions.csv --account "Schwab Taxable" --profile prof_… --as-of 2026-09-18
uv run glassfolio import prices closes.csv                 # date,ticker,close
uv run glassfolio exposure --ticker NVDA --group-by fund
uv run glassfolio check --account "Schwab Taxable" --reported-total 123456.78
uv run glassfolio ops                                      # audit log;  `restore <op_id>` rolls back
uv run glassfolio serve                                    # web UI (build once: pnpm -C web install && pnpm -C web build)
```

### Taxes (planning estimates, not tax advice)

```bash
uv run glassfolio owner add alex --state CA --ltcg 0.15 --ordinary 0.24   # tax assumptions per person
uv run glassfolio import lots lots.csv --account "Schwab Taxable" --as-of 2026-09-18   # optional
uv run glassfolio taxes
```

Without lot details, gains are treated as short-term (switchable per person). In the
UI, the toolbar switches every view between before-tax and after-tax values, and the
Taxes page has per-person assumptions, per-account overrides (including tax-exempt)
and a what-if panel.

### Daily prices and changes

```bash
uv run glassfolio config tiingo                     # free token from tiingo.com; stored in the Keychain
uv run glassfolio daily                              # fetch closes, store today's snapshot, run checks
uv run glassfolio inbox                              # unexplained cash flows and price anomalies
uv run glassfolio inbox answer <id> deposit --remember
uv run glassfolio changes --start 2026-09-01         # price / your money / fund rebalancing per company
uv run glassfolio returns --start 2026-01-01         # TWR and MWR
```

To run `daily` automatically after the US close, add a launchd agent (Glassfolio
never installs one for you). Save this as `~/Library/LaunchAgents/io.glassfolio.daily.plist`,
fix the paths, then run `launchctl load` on it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.glassfolio.daily</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/uv</string><string>run</string><string>--project</string>
    <string>/path/to/Glassfolio</string><string>glassfolio</string><string>daily</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>30</integer></dict>
</dict></plist>
```

`serve` listens on 127.0.0.1 only and prints a one-time link. API calls need the
session cookie set by that link, a known Host header (to block DNS rebinding) and a
same-origin request, so other websites open in your browser can't read your data.

A column mapping is configuration, never code. See
[`tests/golden/broker_profile.json`](tests/golden/broker_profile.json):

```json
{"header_row": 1,
 "columns": {"symbol": "Symbol", "description": "Description", "shares": "Quantity",
             "price": "Price", "market_value": "Market Value", "cost_basis": "Cost Basis"},
 "cash_symbols": ["Cash & Cash Investments"], "skip_symbols": ["Account Total"]}
```

## How exposure is computed

It uses share baskets rather than weights, because weights drift with prices and baskets don't (see [spec §4.1](docs/SPEC.md#41-穿透敞口用股数篮子不用权重)):

```
b(e,s,t) = S(e,s,t) / N(e,t)                       shares of s per ETF share
x(s,t)   = direct(s,t) + Σ_e q(e,t) · b(e,s,t)       your effective shares of s
```

- Uses the latest holdings version on or before the valuation date. Positions, baskets and prices are split-adjusted.
- Falls back to `weight × p(ETF) / p(s)` when share counts are missing (flagged ≈).
- Funds of funds are expanded recursively, up to 5 levels deep. A CIT without holdings maps to an index proxy (flagged ≈).
- Each expanded fund also emits a `fund_residual` line (its cash drag, premium or discount), so the look-through lines always add up to portfolio value. The `conservation` check verifies this.

## Security model

| Layer | How |
| --- | --- |
| Catalog | DuckDB AES-256-GCM encrypted file (`catalog.duckdb`) |
| Data files | DuckLake `ENCRYPTED` Parquet, one key per file |
| Temp files | `temp_file_encryption = true` |
| Key | macOS Keychain (`glassfolio` / `db-key`); `GLASSFOLIO_DB_KEY` for tests only |
| Imported files | Original stored inside the encrypted lake; delete your copy after import |
| Logs | `ops_log` records tool, ids and row counts, never amounts or quantities |

Data lives in `~/Library/Application Support/Glassfolio` (override with `GLASSFOLIO_HOME`).
**This repository contains only code and synthetic data.** Never commit real statements.

## Development

```bash
uv run pytest --cov=glassfolio
```

Tests run on a synthetic [golden portfolio](tests/golden/README.md) with hand-computed
expected values. Design and requirements: [docs/SPEC.md](docs/SPEC.md).
Implementation notes and deviations from the spec: [docs/DECISIONS.md](docs/DECISIONS.md).
