import { useEffect, useMemo, useState } from "react";
import { api, type Attribution, type Basis, type Changes, type Slice } from "../api";
import { Amount, fmtPct } from "../format";

// Validated categorical slots (dataviz validator, light + dark surfaces).
const PARTS = [
  { key: "price_effect", label: "Price", color: "var(--c-price)" },
  { key: "flow_effect", label: "Your money", color: "var(--c-money)" },
  { key: "rebalance_effect", label: "Fund rebalancing", color: "var(--c-rebal)" },
] as const;

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function DivergingBar({ row, scale }: { row: Attribution; scale: number }) {
  const segs = PARTS.map((p) => ({ ...p, v: row[p.key] })).filter((s) => Math.abs(s.v) > 0.005);
  const width = (v: number) => `${(Math.abs(v) / scale) * 50}%`;
  const side = (neg: boolean) => segs.filter((s) => (s.v < 0) === neg);
  return (
    <div className="diverge" role="img"
      aria-label={segs.map((s) => `${s.label} ${money.format(s.v)}`).join(", ")}>
      <div className="diverge-half neg">
        {side(true).map((s) => <i key={s.key} style={{ width: width(s.v), background: s.color }} title={`${s.label}: ${money.format(s.v)}`} />)}
      </div>
      <div className="diverge-zero" />
      <div className="diverge-half pos">
        {side(false).map((s) => <i key={s.key} style={{ width: width(s.v), background: s.color }} title={`${s.label}: ${money.format(s.v)}`} />)}
      </div>
    </div>
  );
}

function History({ slice, start, end }: { slice: Slice; start: string; end: string }) {
  const [points, setPoints] = useState<{ date: string; value: number }[]>([]);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => { api.history(slice).then((p) => setPoints(p.filter((x) => x.date >= start && x.date <= end))); }, [slice, start, end]);
  if (points.length < 2) {
    return <p className="faint">Value over time appears once there are daily snapshots (run <code>glassfolio daily</code>).</p>;
  }
  const W = 640, H = 140, pad = 6;
  const vals = points.map((p) => p.value);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const x = (i: number) => pad + (i / (points.length - 1)) * (W - 2 * pad);
  const y = (v: number) => H - pad - ((v - lo) / span) * (H - 2 * pad);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const h = hover == null ? null : points[hover];
  return (
    <figure className="history">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Portfolio value over time"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const i = Math.round(((e.clientX - r.left) / r.width) * (points.length - 1));
          setHover(Math.max(0, Math.min(points.length - 1, i)));
        }}>
        <path d={path} fill="none" stroke="var(--direct)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {h && <>
          <line x1={x(hover!)} x2={x(hover!)} y1={0} y2={H} stroke="var(--ink-3)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <circle cx={x(hover!)} cy={y(h.value)} r="4" fill="var(--direct)" stroke="var(--sheet)" strokeWidth="2" />
        </>}
      </svg>
      <figcaption className="muted">
        {h ? <>{h.date}: <Amount value={h.value} /></>
          : <>{points[0].date} to {points[points.length - 1].date}; range <Amount value={lo} /> to <Amount value={hi} /></>}
      </figcaption>
    </figure>
  );
}

const daysBetween = (a: string, b: string) => (Date.parse(b) - Date.parse(a)) / 86_400_000;

function defaultStart(asOf: string, statementDates: string[]): string {
  const previous = statementDates.find((d) => d < asOf);  // dates arrive newest first
  if (previous) return previous;
  const d = new Date(asOf); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 10);
}

export default function ChangesView({ asOf, slice, basis, statementDates, onInbox }: {
  asOf: string; slice: Slice; basis: Basis; statementDates: string[]; onInbox: () => void;
}) {
  const [start, setStart] = useState(() => defaultStart(asOf, statementDates));
  const [data, setData] = useState<Changes | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.changes(start, asOf, slice).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [start, asOf, slice]);
  const scale = useMemo(() => Math.max(1, ...(data?.companies ?? []).map((r) =>
    Math.max(PARTS.reduce((s, p) => s + Math.max(0, r[p.key]), 0), PARTS.reduce((s, p) => s - Math.min(0, r[p.key]), 0)))), [data]);

  const r = data?.returns;
  return (
    <>
      <section className="sheet">
        <div className="row" style={{ marginBottom: 16 }}>
          <label className="field">From<input type="date" value={start} max={asOf} onChange={(e) => setStart(e.target.value)} /></label>
          <span className="muted" style={{ paddingBottom: 7 }}>to {asOf} (change the end date in the toolbar)</span>
        </div>
        {error && <p className="error">{error}</p>}
        {r && (
          <dl className="facts">
            <div><dt>Time-weighted return</dt><dd className="num">{r.twr == null ? "—" : fmtPct(r.twr)}</dd></div>
            {daysBetween(r.start, r.end) >= 365
              ? <div><dt>Money-weighted, per year</dt><dd className="num">{r.mwr == null ? "—" : fmtPct(r.mwr)}</dd></div>
              : <div><dt>Money-weighted</dt><dd className="num">{r.mwr_period == null ? "—" : fmtPct(r.mwr_period)}</dd></div>}
            <div><dt>You added</dt><dd className="num"><Amount value={r.net_flows} /></dd></div>
            {basis === "pre"
              ? <div><dt>Value</dt><dd className="num"><Amount value={r.start_value} /> → <Amount value={r.end_value} /></dd></div>
              : <div><dt>Value after tax</dt><dd className="num"><Amount value={data!.after_tax.start} /> → <Amount value={data!.after_tax.end} /></dd></div>}
          </dl>
        )}
        {r && r.open_questions > 0 && (
          <p className="status warn" style={{ marginTop: 14 }}>
            ! {r.open_questions} unexplained cash {r.open_questions === 1 ? "flow needs" : "flows need"} an answer before these returns are reliable.{" "}
            <button className="btn" onClick={onInbox}>Answer</button>
          </p>
        )}
        {r && r.missing_prices > 0 && (
          <p className="status fail" style={{ marginTop: 8 }}>
            ✗ {r.missing_prices} {r.missing_prices === 1 ? "holding has" : "holdings have"} no price in this period, so values are understated. Import or fetch closing prices.
          </p>
        )}
        <History slice={slice} start={start} end={asOf} />
      </section>
      {data && (
        <section className="sheet">
          <h2 className="section-title">Why each holding changed{basis === "after" && <span className="faint" style={{ fontWeight: 400, fontSize: 14 }}> (before tax)</span>}</h2>
          <div className="legend" style={{ marginTop: 0, marginBottom: 10 }}>
            {PARTS.map((p) => <span key={p.key}><i className="swatch" style={{ background: p.color }} /> {p.label}</span>)}
          </div>
          {data.companies.length === 0 ? <p className="empty">No holdings in this period.</p> : (
            <table>
              <thead>
                <tr>
                  <th>Company</th><th className="hide-narrow" style={{ width: "30%" }}>Change, by cause</th>
                  {PARTS.map((p) => <th key={p.key} className="num hide-narrow">{p.label}</th>)}
                  <th className="num">Change</th>
                </tr>
              </thead>
              <tbody>
                {data.companies.map((row) => (
                  <tr key={row.ticker ?? row.name ?? "?"}>
                    <td><div className="ticker">{row.ticker}{row.approx && <span className="approx">≈</span>}{row.missing_price && <span className="status fail" title="Missing price: this row is incomplete"> ✗</span>}</div><div className="name">{row.name}</div></td>
                    <td className="hide-narrow"><DivergingBar row={row} scale={scale} /></td>
                    {PARTS.map((p) => <td key={p.key} className="num hide-narrow"><Amount value={row[p.key]} /></td>)}
                    <td className="num"><Amount value={row.change} /><div className="faint" style={{ fontSize: 12 }}>{row.start_value ? fmtPct(row.change / row.start_value) : "new"}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </>
  );
}
