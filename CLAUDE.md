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
`exposure.py` (incl. `Slice` filters), `recon.py`, `cli.py`,
`server/` (Starlette API + localhost guards), `llm.py` (local model client,
loopback-only), `understand.py` (read any export: saved layout → model → heuristics,
always validated), `model_eval.py` + `evals/` (known-answer model tests), `tax*.py`,
`assistant/` (tools, sql_sandbox, agent loop with grounding guards, evals, mcp_server),
`demo.py` (loads the synthetic golden portfolio). Frontend: `web/` (Vite + React + TS;
glass on frames, near-solid sheets under numbers, tabular numerals).

- Model tests: `uv run glassfolio eval-model [--model NAME | --heuristic]` and
  `uv run glassfolio eval-assistant [--model NAME]` (synthetic data only)
- Any new assistant tool: read tools compute numbers in code/SQL; write tools only
  return proposals that the UI confirms.
- Web: `pnpm -C web build`, then `uv run glassfolio serve`; `./scripts/demo.sh --ui` for synthetic data.

Formulas in spec §4 and checks in §11 need tests on the golden dataset, with
hand-computed expected values.
