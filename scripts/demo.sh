#!/usr/bin/env bash
# Try Glassfolio on the synthetic golden portfolio. Uses a throwaway database
# and a throwaway key; never touches your Keychain or real data.
#   ./scripts/demo.sh         print results in the terminal
#   ./scripts/demo.sh --ui    open the web UI on the demo data
set -euo pipefail
cd "$(dirname "$0")/.."
G=tests/golden
export GLASSFOLIO_HOME="${GLASSFOLIO_HOME:-$(mktemp -d)/glassfolio-demo}"
export GLASSFOLIO_DB_KEY="$(openssl rand -hex 32)"
gf() { uv run --quiet glassfolio "$@"; }

gf owner add alice >/dev/null
gf account add "Alice Taxable" --owner alice --broker Generic --type taxable >/dev/null
gf account add "Alice Roth" --owner alice --broker Generic --type roth_ira >/dev/null
PROFILE=$(gf profile add Generic $G/broker_profile.json)

for d in 2026-08-31 2026-09-17 2026-09-25; do
  gf import etf $G/etf_qqq_$d.csv --etf QQQ --as-of $d --shares-outstanding 1000000 -y >/dev/null
done
gf import etf $G/etf_vti.csv --etf VTI --as-of 2026-09-17 -y >/dev/null
gf import etf $G/etf_gfof_ishares.csv --etf GFOF --format ishares -y >/dev/null
gf import statement $G/broker_alice_taxable.csv --account "Alice Taxable" --profile "$PROFILE" --as-of 2026-09-18 -y >/dev/null
gf import statement $G/broker_alice_roth.csv --account "Alice Roth" --profile "$PROFILE" --as-of 2026-09-12 -y >/dev/null
gf import statement $G/broker_alice_taxable_2026-09-30.csv --account "Alice Taxable" --profile "$PROFILE" --as-of 2026-09-30 -y >/dev/null
gf import prices $G/prices.csv >/dev/null
gf import actions $G/corporate_actions.csv >/dev/null
gf proxy GCIT VTI >/dev/null
for d in 2026-09-12 2026-09-18 2026-09-25 2026-09-30; do gf snapshot --date $d >/dev/null; done

if [[ "${1:-}" == "--ui" ]]; then
  [[ -f web/dist/index.html ]] || (cd web && pnpm install --silent && pnpm build >/dev/null)
  shift
  exec uv run --quiet glassfolio serve "$@"
fi

echo; echo "== All companies, 2026-09-18 =="; gf exposure --as-of 2026-09-18
echo; echo "== NVDA by fund =="; gf exposure --ticker NVDA --group-by fund --as-of 2026-09-18
echo; echo "== Reconcile taxable account against broker total 8,010 =="
gf check --account "Alice Taxable" --as-of 2026-09-18 --reported-total 8010 --reported-cost 5500
echo; echo "== What changed 09-18 → 09-30 (price / your money / fund rebalancing) =="
gf changes --start 2026-09-18 --end 2026-09-30
echo; echo "== Questions (unexplained cash flows) =="; gf inbox
echo; echo "== Audit log =="; gf ops --limit 5
echo; echo "Demo database: $GLASSFOLIO_HOME (key discarded when this shell exits)"
