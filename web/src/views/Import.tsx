import { useEffect, useRef, useState } from "react";
import { api, type FileKind, type Job, type Meta, type ModelInfo, type Reading } from "../api";
import { Amount, fmtPct } from "../format";
import { Icon } from "../icons";
import { JobProgress } from "../jobs";
import { Segmented } from "../Segmented";

const KIND_LABEL: Record<FileKind, string> = { positions: "Positions", lots: "Lot details", fund_holdings: "Fund holdings" };
const FIELD_LABEL: Record<string, string> = {
  symbol: "Symbol", ticker: "Ticker", description: "Name", name: "Name", shares: "Quantity", price: "Price",
  market_value: "Market value", cost_basis: "Total cost", cost: "Total cost", cost_per_share: "Cost per share",
  acquired_date: "Purchase date", weight: "Weight", asset_class: "Asset class", isin: "ISIN", cusip: "CUSIP",
};
const METHOD: Record<string, string> = {
  "pdf-text": "the PDF's text", ocr: "on-device text recognition", mixed: "the PDF's text and text recognition",
};
const SOURCE: Record<Reading["source"], string> = {
  document: "Transcribed from your document; every value was found on its row's line",
  saved: "Recognized: you confirmed this layout before",
  model: "Read by your local model",
  heuristic: "Read with built-in rules (no model)",
  user: "Your corrections",
};

function DropZone({ onFile, job }: { onFile: (f: File) => void; job?: Job }) {
  const [over, setOver] = useState(false);
  if (job?.status === "running") {
    const ignore = (e: React.DragEvent) => e.preventDefault();  // a second file mustn't open in the browser
    return (
      <div className="drop big" onDragOver={ignore} onDrop={ignore}>
        <Icon name="sparkle" size={26} />
        <JobProgress job={job} title={`Reading ${job.label}`} stage={job.stage}
          note="You can leave this page; the reading continues on this Mac." />
      </div>
    );
  }
  return (
    <label className={`drop big${over ? " over" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}>
      <input type="file" accept=".csv,text/csv,text/plain,.pdf,application/pdf,image/*,.heic" hidden onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      <Icon name="sparkle" size={26} />
      <strong>Drop any export here</strong>
      <span className="muted">A CSV, a PDF statement or a screenshot: positions, lot details or a fund's holdings.</span>
    </label>
  );
}

function ColumnPicker({ reading, onChange }: { reading: Reading; onChange: (r: Reading) => void }) {
  const fields = reading.fields[reading.kind];
  const sample = (col: string | null) => {
    const i = col ? reading.header.indexOf(col) : -1;
    return i < 0 ? "" : reading.sample.map((r) => r[i]).filter(Boolean).slice(0, 3).join(", ");
  };
  return (
    <table className="picker">
      <thead><tr><th>Field</th><th>Column in your file</th><th className="hide-narrow">Sample values</th></tr></thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f}>
            <td>{FIELD_LABEL[f] ?? f}</td>
            <td><select className="select" value={reading.columns[f] ?? ""}
              onChange={(e) => onChange({ ...reading, source: "user", columns: { ...reading.columns, [f]: e.target.value || null } })}>
              <option value="">Not in file</option>
              {reading.header.filter(Boolean).map((h) => <option key={h} value={h}>{h}</option>)}
            </select></td>
            <td className="hide-narrow muted name" style={{ maxWidth: "32ch" }}>{sample(reading.columns[f])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Understood({ reading, meta, account, setAccount, onChange }: {
  reading: Reading; meta: Meta; account: string; setAccount: (a: string) => void; onChange: (r: Reading) => void;
}) {
  const [showColumns, setShowColumns] = useState(reading.errors.length > 0 || reading.source === "heuristic");
  const list = (xs: string[]) => xs.join(", ");
  return (
    <section className="sheet">
      <p className="source-badge"><Icon name={reading.source === "saved" ? "reconcile" : "sparkle"} size={16} /> {SOURCE[reading.source]}
        {reading.document && <> (read with {METHOD[reading.document.method] ?? reading.document.method})</>}</p>
      {reading.document && !reading.document.warnings.some((w) => w.includes("total")) &&
        <p className="status pass" style={{ margin: "6px 6px 0" }}>✓ The rows add up to the total printed in the document</p>}
      {reading.document?.warnings.map((w) => <p key={w} className="status warn" style={{ margin: "6px 6px 0" }}>! {w}</p>)}
      {reading.document && reading.document.sources.length > 0 && (
        <details className="sources">
          <summary>Where each row came from</summary>
          {reading.document.sources.map((s) => <p key={s.symbol}><strong>{s.symbol}</strong> <code className="amount">{s.line}</code></p>)}
        </details>
      )}
      {reading.source === "document" ? <p className="muted" style={{ margin: "10px 6px 14px" }}>{KIND_LABEL[reading.kind]}</p> : (
        <div className="row" style={{ margin: "10px 0 14px" }}>
          <Segmented label="Kind of file" value={reading.kind} onChange={(k) => onChange({ ...reading, kind: k, source: "user" })}
            options={(Object.keys(KIND_LABEL) as FileKind[]).map((k) => ({ id: k, label: KIND_LABEL[k] }))} />
        </div>
      )}
      <div className="row">
        {reading.kind !== "fund_holdings" && (
          <label className="field">Account
            <select className="select" value={account} onChange={(e) => setAccount(e.target.value)}>
              {meta.accounts.map((a) => <option key={a.nickname}>{a.nickname}</option>)}
            </select>
          </label>
        )}
        <label className="field">As of
          <input type="date" value={reading.as_of ?? ""} onChange={(e) => onChange({ ...reading, as_of: e.target.value || null })} />
        </label>
        {reading.kind === "positions" && (
          <label className="field">Broker<input value={reading.broker ?? ""} onChange={(e) => onChange({ ...reading, broker: e.target.value })} /></label>
        )}
        {reading.kind === "fund_holdings" && <>
          <label className="field">Fund ticker<input value={reading.fund_ticker ?? ""} style={{ width: 110 }}
            onChange={(e) => onChange({ ...reading, fund_ticker: e.target.value.toUpperCase(), source: "user" })} /></label>
          <label className="field">Fund shares outstanding<input inputMode="decimal" value={reading.shares_outstanding ?? ""}
            placeholder="if shown" onChange={(e) => onChange({ ...reading, shares_outstanding: e.target.value === "" ? null : Number(e.target.value), source: "user" })} /></label>
        </>}
      </div>
      {reading.kind === "positions" && (
        <div className="row" style={{ marginTop: 12 }}>
          <label className="field" style={{ minWidth: 260 }}>Cash rows
            <input value={list(reading.cash_symbols)} onChange={(e) => onChange({ ...reading, source: "user", cash_symbols: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
          </label>
          <label className="field" style={{ minWidth: 260 }}>Rows to skip (totals)
            <input value={list(reading.skip_symbols)} onChange={(e) => onChange({ ...reading, source: "user", skip_symbols: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
          </label>
        </div>
      )}
      {reading.errors.length > 0 && <ul className="error" style={{ margin: "12px 6px 0" }}>{reading.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
      {reading.source !== "document" && <>
        <button className="btn" style={{ marginTop: 14 }} onClick={() => setShowColumns((s) => !s)} aria-expanded={showColumns}>
          {showColumns ? "Hide columns" : "Check columns"}
        </button>
        {showColumns && <div style={{ marginTop: 12 }}><ColumnPicker reading={reading} onChange={onChange} /></div>}
      </>}
    </section>
  );
}

function PreviewTable({ p }: { p: Record<string, any> }) {
  if (p.kind === "positions") return (
    <>
      <h2 className="section-title">Check each security name</h2>
      <table><thead><tr><th>In your file</th><th>Matched to</th><th className="num">Quantity</th><th className="num">Price</th><th className="num">Value</th></tr></thead>
        <tbody>{p.rows.map((r: any, i: number) => (
          <tr key={i}><td><div className="ticker">{r.symbol}</div><div className="name">{r.description}</div></td>
            <td>{r.status === "new" ? <span className="status warn">! New security</span> : r.master_name ?? <span className="faint">known</span>}</td>
            <td className="num"><span className="amount">{Number(r.shares).toLocaleString()}</span></td>
            <td className="num"><span className="amount">{Number(r.price).toLocaleString(undefined, { maximumFractionDigits: 4 })}</span></td>
            <td className="num"><Amount value={Number(r.market_value)} /></td></tr>))}</tbody></table>
      <p style={{ margin: "12px 6px" }}>Total at the file's prices: <strong className="num"><Amount value={Number(p.total_value)} /></strong></p>
      {p.skipped?.length > 0 && <p className="muted" style={{ margin: "0 6px 12px" }}>Skipped as totals: {p.skipped.join(", ")}</p>}
    </>);
  if (p.kind === "lots") return (
    <>
      <h2 className="section-title">{p.count} lots</h2>
      <table><thead><tr><th>Symbol</th><th>Bought</th><th className="num">Quantity</th><th className="num">Cost</th></tr></thead>
        <tbody>{p.lots.map((l: any, i: number) => <tr key={i}><td className="ticker">{l.symbol}</td><td>{l.acquired}</td>
          <td className="num"><span className="amount">{Number(l.shares).toLocaleString()}</span></td><td className="num"><Amount value={Number(l.cost)} /></td></tr>)}</tbody></table>
    </>);
  return (
    <>
      <h2 className="section-title">{p.etf} holdings as of {p.as_of}</h2>
      <p className="muted" style={{ margin: "0 6px 10px" }}>{p.count} holdings, weights add up to {fmtPct(p.weight_sum)}; {p.shares_outstanding ? "exact share counts available." : "no share count, so weights will be used (approximate)."}</p>
      <table><tbody>{p.top.map((t: any, i: number) => <tr key={i}><td className="ticker">{t.ticker}</td><td className="name">{t.name}</td><td className="num">{t.weight == null ? "—" : fmtPct(t.weight)}</td></tr>)}</tbody></table>
    </>);
}

export default function ImportView({ meta, onDone, onSettings, job, onJob, onForget }: {
  meta: Meta; asOf: string; onDone: () => void; onSettings: () => void;
  job?: Job; onJob: (job: Job) => void; onForget: (id: string) => void;
}) {
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [reading, setReading] = useState<Reading | null>(null);
  const [account, setAccount] = useState(meta.accounts[0]?.nickname ?? "");
  const [preview, setPreview] = useState<Record<string, any> | null>(null);
  const shown = useRef<string | null>(null);  // the job whose result is on screen
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.models().then(setModel).catch(() => undefined); }, []);
  useEffect(() => {
    if (!meta.accounts.some((a) => a.nickname === account)) setAccount(meta.accounts[0]?.nickname ?? "");
  }, [meta.accounts, account]);
  useEffect(() => {  // a finished reading shows up here, even if you left while it ran
    if (!job || job.status === "running" || job.id === shown.current) return;
    shown.current = job.id;
    api.job<Reading>(job.id).then((j) => {
      if ("error" in j.result) {
        setError(j.result.error);
        onForget(j.id);  // shown once; a retry starts clean
      } else setReading(j.result);
    }).catch((e) => setError(e.message));
  }, [job, onForget]);
  const fail = (e: Error) => setError(e.message);

  const read = (file: File) => {
    setReading(null); setPreview(null); setMessage(null); setError(null);
    api.readFile(file).then((r) => onJob(r.job)).catch(fail);
  };
  const previewIt = () => reading && api.previewReading({
    token: reading.token, account, broker: reading.broker,
    reading: { ...reading, edited: reading.source === "user" },
  }).then((p) => { setPreview(p); setError(null); }).catch(fail);
  const commit = () => preview && api.commit(preview.token).then(() => {
    setMessage(`${KIND_LABEL[reading!.kind]} imported${reading!.source === "saved" || reading!.source === "document" ? "" : "; this layout will be recognized next time"}. You can delete the file from your disk; an encrypted copy is kept.`);
    setReading(null); setPreview(null); onDone();
    if (shown.current) onForget(shown.current);  // imported: coming back shows an empty drop zone
  }).catch(fail);

  return (
    <>
      <section className="sheet">
        <p className="source-badge" style={{ marginBottom: 12 }}>
          <Icon name="sparkle" size={16} />
          {model?.name ? <>Local model: {model.name}{model.evals[model.name] ? ` (read ${model.evals[model.name].passed}/${model.evals[model.name].total} test files)` : " (not tested yet)"}</>
            : <>No local model chosen: built-in rules will read files.</>}
          <button className="btn" style={{ marginLeft: "auto", minHeight: 28, padding: "3px 12px" }} onClick={onSettings}>Model settings</button>
        </p>
        <DropZone onFile={read} job={job} />
        {message && <p className="status pass" style={{ margin: "12px 6px 0" }}>✓ {message}</p>}
        {error && <p className="error" style={{ margin: "12px 6px 0" }}>{error}</p>}
        {!meta.accounts.length && <p className="status warn" style={{ margin: "12px 6px 0" }}>! Add a person and an account first (Accounts page).</p>}
      </section>
      {reading && <Understood reading={reading} meta={meta} account={account} setAccount={setAccount}
        onChange={(r) => { setReading(r); setPreview(null); }} />}
      {reading && !preview && (
        <div className="row"><button className="btn primary" onClick={previewIt}>Preview import</button></div>
      )}
      {preview && (
        <section className="sheet">
          <PreviewTable p={preview} />
          {preview.errors?.length > 0 && <ul className="error">{preview.errors.map((e: string) => <li key={e}>{e}</li>)}</ul>}
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary" disabled={preview.errors?.length > 0} onClick={commit}>Import</button>
            <button className="btn" onClick={() => setPreview(null)}>Back</button>
          </div>
        </section>
      )}
      <Prices onDone={onDone} />
    </>
  );
}

function Prices({ onDone }: { onDone: () => void }) {
  const [message, setMessage] = useState<string | null>(null);
  return (
    <section className="sheet">
      <h2 className="section-title">Closing prices</h2>
      <p className="muted" style={{ margin: "0 6px 10px" }}>Usually fetched automatically (<code>glassfolio daily</code>). To add your own: a CSV with columns date,ticker,close.</p>
      <label className="btn" style={{ marginLeft: 6 }}>
        <input type="file" accept=".csv" hidden onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) api.upload("/api/import/prices", f, {}).then(() => { setMessage("Prices imported."); onDone(); }).catch((x) => setMessage(x.message));
        }} />Choose prices file
      </label>
      {message && <p className="muted" style={{ margin: "10px 6px 0" }}>{message}</p>}
    </section>
  );
}
