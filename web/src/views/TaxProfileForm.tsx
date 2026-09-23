import type { StateDefault, TaxProfile } from "../api";
import { fmtPct, pctInput } from "../format";
import { Segmented } from "../Segmented";

const LTCG = [0, 0.15, 0.2];
const ORDINARY = [0.1, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37];

export interface ProfileDraft {
  state: string; state_rate: string; federal_ltcg_rate: number; federal_ordinary_rate: number;
  niit: boolean; withdrawal_rate: string; no_lot_assumption: "short_term" | "long_term"; count_losses: boolean;
}

export function draftFrom(p: TaxProfile): ProfileDraft {
  return {
    state: p.state, state_rate: pctInput(p.state_rate), federal_ltcg_rate: p.federal_ltcg_rate,
    federal_ordinary_rate: p.federal_ordinary_rate, niit: p.niit, withdrawal_rate: pctInput(p.withdrawal_rate),
    no_lot_assumption: p.no_lot_assumption, count_losses: p.count_losses,
  };
}

/** Draft → API body (percent inputs become fractions). */
export function toBody(d: ProfileDraft) {
  const frac = (s: string) => {
    if (s.trim() === "") return null;
    const n = Number(s);
    if (!Number.isFinite(n)) throw new Error(`"${s}" is not a number; use a percent like 9.3`);
    return n / 100;
  };
  return { ...d, state_rate: frac(d.state_rate), withdrawal_rate: frac(d.withdrawal_rate) };
}

export function TaxProfileFields({ draft, states, onChange }: {
  draft: ProfileDraft; states: StateDefault[]; onChange: (d: ProfileDraft) => void;
}) {
  const state = states.find((s) => s.code === draft.state);
  const set = <K extends keyof ProfileDraft>(k: K, v: ProfileDraft[K]) => onChange({ ...draft, [k]: v });
  const stateRate = Number(draft.state_rate || 0) / 100;
  const niit = draft.niit ? 0.038 : 0;
  const withdrawal = draft.withdrawal_rate ? Number(draft.withdrawal_rate) / 100 : draft.federal_ordinary_rate + stateRate;
  return (
    <>
      <div className="row">
        <label className="field">State
          <select className="select" value={draft.state} onChange={(e) => {
            const next = states.find((s) => s.code === e.target.value);
            onChange({ ...draft, state: e.target.value, state_rate: next?.rate == null ? "" : pctInput(next.rate) });
          }}>
            {states.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
          </select>
        </label>
        <label className="field">State rate (%)
          <input inputMode="decimal" value={draft.state_rate} placeholder="your bracket" required
            onChange={(e) => set("state_rate", e.target.value)} style={{ width: 120 }} />
        </label>
        <label className="field">Federal long-term gains
          <select className="select" value={draft.federal_ltcg_rate} onChange={(e) => set("federal_ltcg_rate", Number(e.target.value))}>
            {LTCG.map((r) => <option key={r} value={r}>{fmtPct(r)}</option>)}
          </select>
        </label>
        <label className="field">Federal income bracket
          <select className="select" value={draft.federal_ordinary_rate} onChange={(e) => set("federal_ordinary_rate", Number(e.target.value))}>
            {ORDINARY.map((r) => <option key={r} value={r}>{fmtPct(r)}</option>)}
          </select>
        </label>
        <label className="field">Withdrawal rate (%)
          <input inputMode="decimal" value={draft.withdrawal_rate} placeholder={String(Math.round(withdrawal * 1000) / 10)}
            onChange={(e) => set("withdrawal_rate", e.target.value)} style={{ width: 120 }} />
        </label>
      </div>
      {state?.note && <p className="faint" style={{ margin: "8px 6px 0", fontSize: 13 }}>{state.name}: {state.note} ({state.as_of} rates; check yours).</p>}
      <div className="row" style={{ marginTop: 14, alignItems: "center" }}>
        <span className="muted" style={{ fontSize: 13 }}>Without lot details, treat gains as</span>
        <Segmented label="Gains without lot details" value={draft.no_lot_assumption}
          options={[{ id: "short_term", label: "Short-term" }, { id: "long_term", label: "Long-term" }]}
          onChange={(v) => set("no_lot_assumption", v)} />
      </div>
      <div className="row" style={{ marginTop: 12, gap: 20 }}>
        <label className="check"><input type="checkbox" checked={draft.niit} onChange={(e) => set("niit", e.target.checked)} /> Add 3.8% net investment income tax</label>
        <label className="check"><input type="checkbox" checked={draft.count_losses} onChange={(e) => set("count_losses", e.target.checked)} /> Count unrealized losses as a tax saving</label>
      </div>
      <p className="muted" style={{ margin: "12px 6px 0", fontSize: 13 }}>
        Long-term gains {fmtPct(draft.federal_ltcg_rate + niit + stateRate)} , short-term {fmtPct(draft.federal_ordinary_rate + niit + stateRate)} , withdrawals {fmtPct(withdrawal)}
      </p>
    </>
  );
}
