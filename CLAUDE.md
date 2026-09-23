# Glassfolio — notes for coding assistants

Requirements: `docs/SPEC.md` (Chinese). Decisions and deviations: `docs/DECISIONS.md`.

## Hard rules (spec §7, §13)

- **Never read real financial files.** Develop and test only with synthetic data in
  `tests/golden/`. Do not open anything under `~/Library/Application Support/Glassfolio`.
- Every write goes through `lake.run_write` (ops_log + snapshot). No direct writes.
- `ops_log.params` and logs never contain amounts or quantities.
- The model generates configuration (e.g. column-mapping JSON), never code to execute.
- Write tools are two-step: `preview_*` (no writes), then `commit_*` after confirmation.
- Network requests never carry holdings; only whitelisted market/ETF data domains.
- Business tables are append-only; latest row per id wins.
- Work phase by phase (spec §12). Don't implement later phases early.

## Commands

- Tests: `uv run pytest --cov=glassfolio`
- CLI: `uv run glassfolio --help`

## Layout

`src/glassfolio/`: `lake.py` (storage, audited writes, restore), `securities.py`
(identity resolution), `broker_import.py`, `etf_import.py`, `market_import.py`,
`registry.py` (owners/accounts/profiles/proxies), `sql/exposure.sql` (look-through),
`exposure.py`, `recon.py`, `cli.py`.

Formulas in spec §4 and checks in §11 need tests on the golden dataset, with
hand-computed expected values.
