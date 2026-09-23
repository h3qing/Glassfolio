import { useEffect, useState } from "react";
import { api, type ScenarioQuery, type Slice, type TaxOverview } from "../api";
import { accountTypeLabel, Amount, fmtPct, treatmentLabel } from "../format";
import { draftFrom, TaxProfileFields, toBody, type ProfileDraft } from "./TaxProfileForm";

function Totals({ data, scenario }: { data: TaxOverview; scenario: boolean }) {
  const t = data.totals, w = data.what_if;
  return (
    <section className="sheet">
      <dl className="facts">
        <div><dt>Before tax</dt><dd className="num"><Amount value={t.pre_tax} /></dd></div>
        <div><dt>Estimated tax</dt><dd className="num"><Amount value={t.tax} /></dd></div>
        <div><dt>After tax</dt><dd className="num"><Amount value={t.after_tax} /></dd></div>
        {scenario && <div><dt>After tax, what-if</dt><dd className="num"><Amount value={w.after_tax} />{" "}
          <span className="faint" style={{ fontSize: 14 }}>({w.after_tax >= t.after_tax ? "+" : "−"}<Amount value={Math.abs(w.after_tax - t.after_tax)} />)</span></dd></div>}
      </dl>
      <table style={{ marginTop: 16 }}>
        <thead><tr><th>Treatment</th><th className="num">Before tax</th><th className="num">Tax</th><th className="num">Share of tax</th></tr></thead>
        <tbody>
          {Object.entries(t.by_treatment).map(([k, v]) => (
            <tr key={k}><td>{treatmentLabel[k] ?? k}</td><td className="num"><Amount value={v.pre_tax} /></td>
              <td className="num"><Amount value={v.tax} /></td><td className="num">{fmtPct(t.tax ? v.tax / t.tax : 0)}</td></tr>
          ))}
        </tbody>
      </table>
      {t.missing_cost > 0 && <p className="status warn" style={{ margin: "12px 6px 0" }}>! {t.missing_cost} taxable {t.missing_cost === 1 ? "holding has" : "holdings have"} no cost basis; their gains are left out.</p>}
      <p className="faint" style={{ margin: "12px 6px 0", fontSize: 13 }}>Planning estimates from your assumptions, not tax advice.</p>
    </section>
  );
}

type RateKey = "ltcg" | "ordinary" | "state_rate" | "withdrawal";

/** Percent text as typed → fraction for the API (only once it parses). */
function toScenario(text: Partial<Record<RateKey, string>>): ScenarioQuery {
  return Object.fromEntries(Object.entries(text)
    .filter(([, v]) => v !== undefined && v.trim() !== "" && Number.isFinite(Number(v)))
    .map(([k, v]) => [k, String(Number(v) / 100)]));
}

function WhatIf({ onChange }: { onChange: (s: ScenarioQuery) => void }) {
  const [text, setText] = useState<Partial<Record<RateKey, string>>>({});
  const update = (next: Partial<Record<RateKey, string>>) => { setText(next); onChange(toScenario(next)); };
  const field = (key: RateKey, label: string) => {
    const bad = text[key] && !Number.isFinite(Number(text[key]));
    return (
      <label className="field">{label}
        <input inputMode="decimal" placeholder="as saved" style={{ width: 120 }} aria-invalid={bad || undefined}
          value={text[key] ?? ""} onChange={(e) => update({ ...text, [key]: e.target.value })} />
      </label>
    );
  };
  return (
    <section className="sheet">
      <h2 className="section-title">What if</h2>
      <p className="muted" style={{ margin: "0 6px 12px" }}>Try different rates for everyone; nothing is saved.</p>
      <div className="row">
        {field("ltcg", "Federal long-term (%)")}{field("ordinary", "Federal income (%)")}
        {field("state_rate", "State (%)")}{field("withdrawal", "Withdrawal (%)")}
        <button className="btn" onClick={() => update({})} disabled={!Object.values(text).some(Boolean)}>Reset</button>
      </div>
    </section>
  );
}

function Person({ owner, data, onSaved }: { owner: string; data: TaxOverview; onSaved: () => void }) {
  const pid = data.people.find((p) => p.owner === owner)?.profile_id;
  const profile = data.profiles.find((p) => p.tax_profile_id === pid) ?? data.default_profile;
  const [draft, setDraft] = useState<ProfileDraft>(draftFrom(profile));
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="sheet">
      <h2 className="section-title">{owner} <span className="faint" style={{ fontWeight: 400, fontSize: 14 }}>{pid ? profile.name : "using the California default"}</span></h2>
      <TaxProfileFields draft={draft} states={data.states} onChange={setDraft} />
      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={() => {
          // Saved as a new profile: an existing one may be shared with other accounts.
          try {
            api.saveProfile({ ...toBody(draft), name: `${owner} (${draft.state})`, owner })
              .then(() => { setError(null); onSaved(); }).catch((e) => setError(e.message));
          } catch (e) { setError((e as Error).message); }
        }}>
          Save assumptions
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function Accounts({ data, onSaved }: { data: TaxOverview; onSaved: () => void }) {
  return (
    <section className="sheet">
      <h2 className="section-title">How each account is taxed</h2>
      <table>
        <thead><tr><th>Account</th><th>Type</th><th>Taxed as</th><th>Assumptions</th></tr></thead>
        <tbody>
          {data.accounts.map((a) => (
            <tr key={a.nickname}>
              <td className="ticker">{a.nickname}<div className="name">{a.owner}</div></td>
              <td>{accountTypeLabel[a.account_type] ?? a.account_type}</td>
              <td><select className="select" value={a.treatment ?? ""} onChange={(e) => api.setTreatment(a.nickname, e.target.value || null).then(onSaved)}>
                <option value="">{treatmentLabel[a.default_treatment]} (default)</option>
                {Object.entries(treatmentLabel).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select></td>
              <td><select className="select" value={a.profile_id ?? ""} onChange={(e) => api.assignProfile({ profile_id: e.target.value, account: a.nickname }).then(onSaved)}>
                <option value="">{a.owner}'s</option>
                {data.profiles.map((p) => <option key={p.tax_profile_id!} value={p.tax_profile_id!}>{p.name}</option>)}
              </select></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Positions({ data }: { data: TaxOverview }) {
  const rows = data.positions.filter((p) => p.value !== 0);
  return (
    <section className="sheet">
      <h2 className="section-title">By holding</h2>
      <table>
        <thead><tr><th>Holding</th><th className="hide-narrow">Gain basis</th><th className="num hide-narrow">Long-term gain</th>
          <th className="num hide-narrow">Short-term gain</th><th className="num">Tax</th><th className="num">After tax</th></tr></thead>
        <tbody>
          {rows.map((p, i) => (
            <tr key={i}>
              <td><div className="ticker">{p.ticker}</div><div className="name">{p.account}, {treatmentLabel[p.treatment].toLowerCase()}</div></td>
              <td className="hide-narrow muted">{p.basis}</td>
              <td className="num hide-narrow"><Amount value={p.lt_gain} /></td>
              <td className="num hide-narrow"><Amount value={p.st_gain} /></td>
              <td className="num"><Amount value={p.tax} /></td>
              <td className="num"><Amount value={p.after_tax} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function TaxesView({ asOf, slice }: { asOf: string; slice: Slice }) {
  const [data, setData] = useState<TaxOverview | null>(null);
  const [scenario, setScenario] = useState<ScenarioQuery>({});
  const [version, setVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.taxes(asOf, slice, scenario).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [asOf, slice, scenario, version]);
  const saved = () => setVersion((v) => v + 1);
  if (error) return <section className="sheet"><p className="error">{error}</p></section>;
  if (!data) return <section className="sheet"><p className="muted">Estimating…</p></section>;
  return (
    <>
      <Totals data={data} scenario={Object.values(scenario).some(Boolean)} />
      <WhatIf onChange={setScenario} />
      {data.people.map((p) => <Person key={`${p.owner}-${version}`} owner={p.owner} data={data} onSaved={saved} />)}
      <Accounts data={data} onSaved={saved} />
      <Positions data={data} />
    </>
  );
}
