import { useEffect, useState } from "react";
import { api, type InboxItem } from "../api";
import { Amount, fmtPct } from "../format";

function FlowQuestion({ item, onDone }: { item: InboxItem; onDone: () => void }) {
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const p = item.payload;
  const incoming = p.amount > 0;
  const answer = (classification: string, pair?: string) =>
    api.answer({ item_id: item.item_id, classification, pair, remember }).then(onDone).catch((e) => setError(e.message));
  return (
    <section className="sheet">
      <h2 className="section-title">
        {p.account}: <Amount value={Math.abs(p.amount)} /> {incoming ? "more" : "less"} than prices explain
      </h2>
      <p className="muted" style={{ marginTop: 0 }}>Between the statements of {p.start} and {p.end}. What was it?</p>
      <div className="row">
        <button className="btn primary" onClick={() => answer(incoming ? "deposit" : "withdrawal")}>
          {incoming ? "Money I added" : "Money I took out"}
        </button>
        {item.pair_candidates.map((c) => (
          <button key={c.item_id} className="btn" onClick={() => answer("transfer", c.item_id)}>
            Moved {incoming ? "from" : "to"} {c.account}
          </button>
        ))}
        {incoming && <button className="btn" onClick={() => answer("dividend")}>Dividends or interest</button>}
        <button className="btn" onClick={() => answer("not_a_flow")}>Not a cash flow</button>
        <label className="muted" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Treat future {incoming ? "increases" : "decreases"} in this account the same way
        </label>
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function OtherItem({ item, onDone }: { item: InboxItem; onDone: () => void }) {
  const p = item.payload;
  const text = item.type === "price_review"
    ? `${p.ticker} moved ${fmtPct(p.change)} on ${p.date}. Check it's not a data error (no split was reported).`
    : p.message ?? item.type;
  return (
    <section className="sheet">
      <p style={{ margin: "0 0 12px" }}><span className="status warn">!</span> {text}</p>
      <button className="btn" onClick={() => api.answer({ item_id: item.item_id, classification: "not_a_flow" }).then(onDone)}>
        Mark as checked
      </button>
    </section>
  );
}

export default function InboxView({ onChange }: { onChange: () => void }) {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const load = () => api.inbox().then(setItems);
  useEffect(() => { load(); }, []);
  const done = () => { load(); onChange(); };
  if (!items) return <section className="sheet"><p className="muted">Loading…</p></section>;
  if (!items.length) return <section className="sheet"><p className="empty">Nothing to answer. New questions appear here when an import leaves something unexplained.</p></section>;
  return <>{items.map((i) => i.type === "unexplained_flow"
    ? <FlowQuestion key={i.item_id} item={i} onDone={done} />
    : <OtherItem key={i.item_id} item={i} onDone={done} />)}</>;
}
