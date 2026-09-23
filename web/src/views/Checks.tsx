import { useEffect, useState } from "react";
import { api, type CheckReport, type CheckRow, type Meta } from "../api";
import { Amount, StatusBadge } from "../format";

const CHECK_LABEL: Record<string, string> = {
  account_total: "Account total matches broker", cost_total: "Cost basis matches broker",
  statement_date: "Statement is from this date", conservation: "Look-through adds up to the total",
  prices: "Every holding has a price", approximation: "Exact fund holdings",
  fund_depth: "Fund nesting within 5 levels", statement: "Statement imported",
};

const BROKER_CHECKS = new Set(["account_total", "cost_total"]);

function Results({ rows }: { rows: { check_type: string; status: CheckRow["status"]; expected: number | null; actual: number | null; hint: string | null }[] }) {
  return (
    <table>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td style={{ width: 150 }}><StatusBadge status={r.status} label={r.status === "pass" ? "Pass" : r.status === "warn" ? "Note" : "Fail"} /></td>
            <td>{CHECK_LABEL[r.check_type] ?? r.check_type}{r.hint && <div className="muted" style={{ fontSize: 13 }}>{r.hint}</div>}</td>
            <td className="num">{r.expected != null && <>{BROKER_CHECKS.has(r.check_type) ? "broker" : "positions"} <Amount value={r.expected} /></>}</td>
            <td className="num">{r.actual != null && <>{BROKER_CHECKS.has(r.check_type) ? "ours" : "looked through"} <Amount value={r.actual} /></>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ChecksView({ meta, asOf }: { meta: Meta; asOf: string }) {
  const [form, setForm] = useState({ account: meta.accounts[0]?.nickname ?? "", reported_total: "", reported_cost: "" });
  const [report, setReport] = useState<CheckReport | null>(null);
  const [latest, setLatest] = useState<CheckRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.checks().then(setLatest); }, [report]);

  const byScope = latest.reduce<Record<string, CheckRow[]>>((acc, r) => ({ ...acc, [r.scope]: [...(acc[r.scope] ?? []), r] }), {});
  return (
    <>
      <section className="sheet">
        <h2 className="section-title">Check an account against your broker</h2>
        <p className="muted" style={{ marginTop: 0 }}>Enter the total your broker's website shows for {asOf}. Numbers here are trusted only once they match.</p>
        <form className="row" onSubmit={(e) => {
          e.preventDefault();
          api.runChecks({ as_of: asOf, ...form }).then((r) => { setReport(r); setError(null); }).catch((x) => setError(x.message));
        }}>
          <label className="field">Account
            <select className="select" value={form.account} onChange={(e) => setForm({ ...form, account: e.target.value })}>
              <option value="">Whole portfolio</option>
              {meta.accounts.map((a) => <option key={a.nickname}>{a.nickname}</option>)}
            </select>
          </label>
          <label className="field">Broker total<input inputMode="decimal" value={form.reported_total} onChange={(e) => setForm({ ...form, reported_total: e.target.value })} placeholder="123456.78" /></label>
          <label className="field">Broker cost basis (optional)<input inputMode="decimal" value={form.reported_cost} onChange={(e) => setForm({ ...form, reported_cost: e.target.value })} /></label>
          <button className="btn primary">Run checks</button>
        </form>
        {error && <p className="error">{error}</p>}
        {report && <div style={{ marginTop: 18 }}><StatusBadge status={report.status} /><Results rows={report.results} /></div>}
      </section>
      {Object.entries(byScope).map(([scope, rows]) => (
        <section className="sheet" key={scope}>
          <h2 className="section-title">{rows[0].account ?? "Whole portfolio"} <span className="faint" style={{ fontWeight: 400, fontSize: 14 }}>as of {rows[0].as_of}</span></h2>
          <Results rows={rows} />
        </section>
      ))}
    </>
  );
}
