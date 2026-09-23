import { useState } from "react";
import { api, type Meta } from "../api";
import { accountTypeLabel } from "../format";

export default function AccountsView({ meta, onChange }: { meta: Meta; onChange: () => void }) {
  const [person, setPerson] = useState("");
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
      <section className="sheet">
        <h2 className="section-title">Add a person</h2>
        <form className="row" onSubmit={(e) => { e.preventDefault(); run(api.addOwner(person), () => setPerson("")); }}>
          <label className="field">Name<input value={person} onChange={(e) => setPerson(e.target.value)} required /></label>
          <button className="btn">Add person</button>
        </form>
      </section>
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
