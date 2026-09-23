import { useState } from "react";
import { api, type Meta } from "../api";
import { Amount, fmtPct } from "../format";
import { Segmented } from "../Segmented";

type Kind = "statement" | "etf" | "prices";
const KINDS: { id: Kind; label: string; hint: string }[] = [
  { id: "statement", label: "Position statement", hint: "Your broker's positions export as CSV. It must include cash, cost basis and one row per holding." },
  { id: "etf", label: "Fund holdings", hint: "The holdings file from the fund's own website. For iShares, choose iShares format; otherwise use columns ticker,name,asset_class,shares,weight,price,isin." },
  { id: "prices", label: "Closing prices", hint: "CSV with columns date,ticker,close." },
];

interface StatementPreview {
  token: string; total_value: number; as_of: string; errors: string[];
  rows: { symbol: string; description: string | null; master_name: string | null; status: string; shares: number; price: number; market_value: number }[];
}
interface EtfPreview {
  token: string; etf: string; as_of: string; count: number; weight_sum: number;
  shares_outstanding: number | null; errors: string[]; top: { ticker: string | null; name: string | null; weight: number | null }[];
}

function DropZone({ file, onFile }: { file: File | null; onFile: (f: File) => void }) {
  const [over, setOver] = useState(false);
  return (
    <label className={`drop${over ? " over" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}>
      <input type="file" accept=".csv,text/csv" hidden onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      {file ? <>Selected <strong>{file.name}</strong>. Drop another to replace it.</> : "Drop a CSV here, or click to choose one."}
    </label>
  );
}

function StatementTable({ p }: { p: StatementPreview }) {
  return (
    <table>
      <thead><tr><th>In your file</th><th>Matched to</th><th className="num">Shares</th><th className="num">Value</th></tr></thead>
      <tbody>
        {p.rows.map((r, i) => (
          <tr key={i}>
            <td><div className="ticker">{r.symbol}</div><div className="name">{r.description}</div></td>
            <td>{r.status === "new" ? <span className="status warn">! New security</span> : r.master_name ?? <span className="faint">known, no name yet</span>}</td>
            <td className="num"><span className="amount">{r.shares.toLocaleString()}</span></td>
            <td className="num"><Amount value={r.market_value} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ImportView({ meta, asOf, onDone }: { meta: Meta; asOf: string; onDone: () => void }) {
  const [kind, setKind] = useState<Kind>("statement");
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({ account: meta.accounts[0]?.nickname ?? "", profile_id: meta.profiles[0]?.profile_id ?? "",
    as_of: asOf, etf: "", format: "generic", shares_outstanding: "", broker: "", mapping: "" });
  const [preview, setPreview] = useState<StatementPreview | EtfPreview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const fail = (e: Error) => setError(e.message);
  const reset = () => { setPreview(null); setFile(null); setError(null); };

  const check = () => {
    if (!file) return;
    setMessage(null); setError(null);
    if (kind === "prices") {
      api.upload<{ op_id: string }>("/api/import/prices", file, {}).then(() => { setMessage("Prices imported."); reset(); onDone(); }).catch(fail);
    } else if (kind === "statement") {
      api.upload<StatementPreview>("/api/import/statement", file, { account: f.account, profile_id: f.profile_id, as_of: f.as_of }).then(setPreview).catch(fail);
    } else {
      api.upload<EtfPreview>("/api/import/etf", file, { etf: f.etf, format: f.format, as_of: f.as_of, shares_outstanding: f.shares_outstanding }).then(setPreview).catch(fail);
    }
  };
  const confirm = () => preview && api.commit(preview.token)
    .then(() => { setMessage(kind === "statement" ? "Statement imported. You can delete the file from your disk; a copy is kept encrypted." : "Fund holdings published."); reset(); onDone(); })
    .catch(fail);
  const saveMapping = () => api.addProfile(f.broker, f.mapping)
    .then((r) => { setF({ ...f, profile_id: r.profile_id, mapping: "" }); onDone(); }).catch(fail);

  return (
    <>
      <section className="sheet">
        <Segmented label="What to import" value={kind} options={KINDS.map(({ id, label }) => ({ id, label }))}
          onChange={(k) => { setKind(k); reset(); }} />
        <p className="muted" style={{ margin: "14px 6px" }}>{KINDS.find((k) => k.id === kind)!.hint}</p>
        <DropZone file={file} onFile={(x) => { setFile(x); setPreview(null); }} />
        <div className="row" style={{ marginTop: 14 }}>
          {kind === "statement" && <>
            <label className="field">Account<select className="select" value={f.account} onChange={set("account")}>{meta.accounts.map((a) => <option key={a.nickname}>{a.nickname}</option>)}</select></label>
            <label className="field">Column mapping<select className="select" value={f.profile_id} onChange={set("profile_id")}>
              <option value="">Choose…</option>
              {meta.profiles.map((p) => <option key={p.profile_id} value={p.profile_id}>{p.broker} ({p.profile_id.slice(-6)})</option>)}
            </select></label>
          </>}
          {kind === "etf" && <>
            <label className="field">Fund ticker<input value={f.etf} onChange={set("etf")} placeholder="QQQ" required /></label>
            <label className="field">Format<select className="select" value={f.format} onChange={set("format")}><option value="generic">Generic columns</option><option value="ishares">iShares</option></select></label>
            {f.format === "generic" && <label className="field">Fund shares outstanding<input inputMode="decimal" value={f.shares_outstanding} onChange={set("shares_outstanding")} placeholder="optional" /></label>}
          </>}
          {kind !== "prices" && !(kind === "etf" && f.format === "ishares") &&
            <label className="field">As of<input type="date" value={f.as_of} onChange={set("as_of")} /></label>}
          <button className="btn primary" disabled={!file} onClick={check}>{kind === "prices" ? "Import prices" : "Preview"}</button>
        </div>
        {error && <p className="error">{error}</p>}
        {message && <p className="status pass">✓ {message}</p>}
      </section>

      {preview && (
        <section className="sheet">
          {"rows" in preview ? <>
            <h2 className="section-title">Check each security name before importing</h2>
            <StatementTable p={preview} />
            <p>Total at the file's prices: <strong className="num"><Amount value={preview.total_value} /></strong></p>
          </> : <>
            <h2 className="section-title">{preview.etf} holdings as of {preview.as_of}</h2>
            <p className="muted">{preview.count} holdings, weights add up to {fmtPct(preview.weight_sum)}; {preview.shares_outstanding ? "exact share counts available." : "no share count, so weights will be used (approximate)."}</p>
            <table><tbody>{preview.top.map((t, i) => <tr key={i}><td className="ticker">{t.ticker}</td><td className="name">{t.name}</td><td className="num">{t.weight == null ? "—" : fmtPct(t.weight)}</td></tr>)}</tbody></table>
          </>}
          {preview.errors.length > 0 && <ul className="error">{preview.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary" disabled={preview.errors.length > 0} onClick={confirm}>{"rows" in preview ? "Import statement" : "Publish holdings"}</button>
            <button className="btn" onClick={reset}>Cancel</button>
          </div>
        </section>
      )}

      {kind === "statement" && (
        <section className="sheet">
          <h2 className="section-title">New broker? Save its column mapping</h2>
          <p className="muted" style={{ marginTop: 0 }}>Tell Glassfolio which column holds what. This is saved once per broker and reused.</p>
          <div className="row">
            <label className="field">Broker<input value={f.broker} onChange={set("broker")} /></label>
            <label className="field" style={{ flex: 1, minWidth: 280 }}>Mapping (JSON)
              <textarea rows={6} value={f.mapping} onChange={set("mapping")} spellCheck={false}
                placeholder={'{"header_row": 1, "columns": {"symbol": "Symbol", "description": "Description", "shares": "Quantity", "price": "Price", "market_value": "Market Value", "cost_basis": "Cost Basis"}, "cash_symbols": ["Cash & Cash Investments"], "skip_symbols": ["Account Total"]}'} />
            </label>
            <button className="btn" disabled={!f.broker || !f.mapping} onClick={saveMapping}>Save mapping</button>
          </div>
        </section>
      )}
    </>
  );
}
