import { useEffect, useState } from "react";
import { api, type FileKind, type Meta, type ModelInfo, type Reading } from "../api";
import { Amount, fmtPct } from "../format";
import { Icon } from "../icons";
import { Segmented } from "../Segmented";

const KIND_LABEL: Record<FileKind, string> = { positions: "Positions", lots: "Lot details", fund_holdings: "Fund holdings" };
const FIELD_LABEL: Record<string, string> = {
  symbol: "Symbol", ticker: "Ticker", description: "Name", name: "Name", shares: "Quantity", price: "Price",
  market_value: "Market value", cost_basis: "Total cost", cost: "Total cost", cost_per_share: "Cost per share",
  acquired_date: "Purchase date", weight: "Weight", asset_class: "Asset class", isin: "ISIN", cusip: "CUSIP",
};
const SOURCE: Record<Reading["source"], string> = {
  saved: "Recognized: you confirmed this layout before",
  model: "Read by your local model",
  heuristic: "Read with built-in rules (no model)",
  user: "Your corrections",
};

function DropZone({ onFile, busy }: { onFile: (f: File) => void; busy: string | null }) {
  const [over, setOver] = useState(false);
  return (
    <label className={`drop big${over ? " over" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}>
      <input type="file" accept=".csv,text/csv,text/plain" hidden onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      <Icon name="sparkle" size={26} />
      <strong>{busy ?? "Drop any export here"}</strong>
      <span className="muted">{busy ? "The file stays on this Mac." : "Positions, lot details or a fund's holdings, from any broker or fund company."}</span>
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
      <p className="source-badge"><Icon name={reading.source === "saved" ? "reconcile" : "sparkle"} size={16} /> {SOURCE[reading.source]}</p>
      <div className="row" style={{ margin: "10px 0 14px" }}>
        <Segmented label="Kind of file" value={reading.kind} onChange={(k) => onChange({ ...reading, kind: k, source: "user" })}
          options={(Object.keys(KIND_LABEL) as FileKind[]).map((k) => ({ id: k, label: KIND_LABEL[k] }))} />
      </div>
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
      <button className="btn" style={{ marginTop: 14 }} onClick={() => setShowColumns((s) => !s)} aria-expanded={showColumns}>
        {showColumns ? "Hide columns" : "Check columns"}
      </button>
      {showColumns && <div style={{ marginTop: 12 }}><ColumnPicker reading={reading} onChange={onChange} /></div>}
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

export default function ImportView({ meta, onDone, onSettings }: { meta: Meta; asOf: string; onDone: () => void; onSettings: () => void }) {
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [reading, setReading] = useState<Reading | null>(null);
  const [account, setAccount] = useState(meta.accounts[0]?.nickname ?? "");
  const [preview, setPreview] = useState<Record<string, any> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.models().then(setModel).catch(() => undefined); }, []);
  useEffect(() => {
    if (!meta.accounts.some((a) => a.nickname === account)) setAccount(meta.accounts[0]?.nickname ?? "");
  }, [meta.accounts, account]);
  const fail = (e: Error) => { setError(e.message); setBusy(null); };

  const read = (file: File) => {
    setReading(null); setPreview(null); setMessage(null); setError(null);
    setBusy(model?.name ? `Reading ${file.name} with ${model.name}…` : `Reading ${file.name}…`);
    api.readFile(file).then((r) => { setReading(r); setBusy(null); }).catch(fail);
  };
  const previewIt = () => reading && api.previewReading({
    token: reading.token, account, broker: reading.broker,
    reading: { ...reading, edited: reading.source === "user" },
  }).then((p) => { setPreview(p); setError(null); }).catch(fail);
  const commit = () => preview && api.commit(preview.token).then(() => {
    setMessage(`${KIND_LABEL[reading!.kind]} imported${reading!.source === "saved" ? "" : "; this layout will be recognized next time"}. You can delete the file from your disk; an encrypted copy is kept.`);
    setReading(null); setPreview(null); onDone();
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
        <DropZone onFile={read} busy={busy} />
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
