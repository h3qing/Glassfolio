import { useEffect, useState } from "react";
import { api, type Meta, type TaxOverview } from "../api";
import { draftFrom, TaxProfileFields, toBody, type ProfileDraft } from "./TaxProfileForm";
import { accountTypeLabel } from "../format";

function AddPerson({ onDone }: { onDone: () => void }) {
  const [overview, setOverview] = useState<TaxOverview | null>(null);
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.taxes("", {}).then((o) => { setOverview(o); setDraft(draftFrom(o.default_profile)); }); }, []);
  if (!overview || !draft) return null;
  return (
    <section className="sheet">
      <h2 className="section-title">Add a person</h2>
      <p className="muted" style={{ margin: "0 6px 12px" }}>Their tax assumptions set every after-tax number. You can change them later under Taxes.</p>
      <form onSubmit={(e) => {
        e.preventDefault();
        try {
          api.addPerson({ nickname: name, ...toBody(draft) })
            .then(() => { setName(""); setError(null); onDone(); }).catch((x) => setError(x.message));
        } catch (x) { setError((x as Error).message); }
      }}>
        <div className="row" style={{ marginBottom: 12 }}>
          <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        </div>
        <TaxProfileFields draft={draft} states={overview.states} onChange={setDraft} />
        <div className="row" style={{ marginTop: 14 }}><button className="btn primary">Add person</button></div>
      </form>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

export default function AccountsView({ meta, onChange }: { meta: Meta; onChange: () => void }) {
  const [acct, setAcct] = useState({ nickname: "", owner: meta.owners[0] ?? "", broker: "", account_type: "taxable" });
  const [error, setError] = useState<string | null>(null);

  const run = (p: Promise<unknown>, reset: () => void) =>
    p.then(() => { reset(); setError(null); onChange(); }).catch((e) => setError(e.message));

  return (
    <>
      <section className="sheet">
        {meta.accounts.length ? (
          <table>
            <thead><tr><th>Account</th><th>Person</th><th>Type</th><th>Broker</th><th className="num">Latest statement</th></tr></thead>
            <tbody>
              {meta.accounts.map((a) => (
                <tr key={a.nickname}>
                  <td className="ticker">{a.nickname}</td><td>{a.owner}</td>
                  <td>{accountTypeLabel[a.account_type] ?? a.account_type}</td><td>{a.broker}</td>
                  <td className="num">{a.latest_statement ?? <span className="faint">none yet</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="empty">No accounts yet. Add a person first, then their accounts. Nicknames only; never enter account numbers.</p>}
      </section>
      <AddPerson onDone={onChange} />
      <section className="sheet">
        <h2 className="section-title">Add an account</h2>
        <form className="row" onSubmit={(e) => {
          e.preventDefault();
          run(api.addAccount(acct), () => setAcct({ ...acct, nickname: "", broker: "" }));
        }}>
          <label className="field">Nickname<input value={acct.nickname} onChange={(e) => setAcct({ ...acct, nickname: e.target.value })} placeholder="Schwab taxable" required /></label>
          <label className="field">Person
            <select className="select" value={acct.owner} onChange={(e) => setAcct({ ...acct, owner: e.target.value })} required>
              <option value="" disabled>Choose…</option>
              {meta.owners.map((o) => <option key={o}>{o}</option>)}
            </select>
          </label>
          <label className="field">Type
            <select className="select" value={acct.account_type} onChange={(e) => setAcct({ ...acct, account_type: e.target.value })}>
              {meta.account_types.map((t) => <option key={t} value={t}>{accountTypeLabel[t] ?? t}</option>)}
            </select>
          </label>
          <label className="field">Broker<input value={acct.broker} onChange={(e) => setAcct({ ...acct, broker: e.target.value })} required /></label>
          <button className="btn" disabled={!meta.owners.length}>Add account</button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>
    </>
  );
}
