import { Fragment, useEffect, useState } from "react";
import { api, type Company, type CompanyDetail, type Exposure, type Slice } from "../api";
import { Amount, fmtPct } from "../format";

function SplitBar({ c, max }: { c: Company; max: number }) {
  const width = max > 0 ? (c.total / max) * 100 : 0;
  const direct = c.total > 0 ? (c.direct_value / c.total) * 100 : 0;
  return (
    <div className="split" style={{ width: `${Math.max(width, 0.5)}%` }}
      title={`${fmtPct(direct / 100)} held directly, ${fmtPct(1 - direct / 100)} through funds`}>
      {c.direct_value > 0 && <div className="direct" style={{ width: `${direct}%` }} />}
      {c.via_fund_value > 0 && <div className="via" style={{ flex: 1 }} />}
    </div>
  );
}

function Headline({ data }: { data: Exposure }) {
  const top = data.companies[0];
  if (!top || data.summary.total_value <= 0) return null;
  const share = top.total / data.summary.total_value;
  const inside = top.total > 0 ? top.via_fund_value / top.total : 0;
  const tail = inside > 0.5 ? `and ${fmtPct(inside)} of it sits inside funds.`
    : inside > 0 ? `${fmtPct(inside)} of it through funds.` : "all of it held directly.";
  return <h2 className="headline">{top.ticker} is your largest real holding: {fmtPct(share)} of everything, {tail}</h2>;
}

function Breakdown({ rows, total, label }: { rows: Company[]; total: number; label: string }) {
  return (
    <div>
      <h3>{label}</h3>
      <table>
        <tbody>
          {rows.map((r) => (
            <tr key={r.group ?? "—"}>
              <td>{r.group === "direct" ? "Held directly" : r.group}{r.approx && <span className="approx">≈</span>}</td>
              <td className="num"><Amount value={r.total} /></td>
              <td className="num muted">{fmtPct(total ? r.total / total : NaN)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ ticker, asOf, slice }: { ticker: string; asOf: string; slice: Slice }) {
  const [d, setD] = useState<CompanyDetail | null>(null);
  useEffect(() => { api.company(ticker, asOf, slice).then(setD); }, [ticker, asOf, slice]);
  if (!d) return <p className="muted">Loading…</p>;
  const total = d.fund.reduce((s, r) => s + r.total, 0);
  return (
    <div className="drawer">
      <Breakdown rows={d.fund} total={total} label="Where it comes from" />
      <Breakdown rows={d.account} total={total} label="Which account" />
      <Breakdown rows={d.owner} total={total} label="Whose" />
    </div>
  );
}

export default function ExposureView({ asOf, slice, onImport }: { asOf: string; slice: Slice; onImport: () => void }) {
  const [data, setData] = useState<Exposure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    api.exposure(asOf, slice).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [asOf, slice]);

  if (error) return <section className="sheet"><p className="error">{error}</p></section>;
  if (!data) return <section className="sheet"><p className="muted">Looking through your funds…</p></section>;
  const { summary, companies } = data;
  if (!companies.length) {
    return (
      <section className="sheet">
        <p className="empty">Nothing to show for {asOf}. Import a position statement and the holdings of the funds you own, then come back here.</p>
        <button className="btn primary" onClick={onImport}>Import files</button>
      </section>
    );
  }
  const max = companies[0].total;
  const funds = companies.reduce((s, c) => s + c.via_fund_value, 0);
  return (
    <>
      <section className="sheet">
        <Headline data={data} />
        <dl className="facts">
          <div><dt>Portfolio</dt><dd className="num"><Amount value={summary.total_value} /></dd></div>
          <div><dt>Through funds</dt><dd className="num">{fmtPct(funds / summary.total_value)}</dd></div>
          <div><dt>Cash</dt><dd className="num">{fmtPct(summary.cash_value / summary.total_value)}</dd></div>
          <div><dt>Approximate</dt><dd className="num">{fmtPct(summary.approx_value / summary.total_value)}</dd></div>
          {summary.missing_prices > 0 && (
            <div><dt>Missing prices</dt><dd className="status fail">✗ {summary.missing_prices}</dd></div>
          )}
        </dl>
      </section>
      <section className="sheet">
        <table>
          <thead>
            <tr>
              <th>Company</th>
              <th style={{ width: "34%" }} className="hide-narrow">Direct and through funds</th>
              <th className="num">Held directly</th>
              <th className="num hide-narrow">Through funds</th>
              <th className="num">Total</th>
              <th className="num">Share</th>
            </tr>
          </thead>
          <tbody>
            {companies.map((c) => {
              const key = c.ticker ?? c.name ?? "?";
              const isOpen = open === key;
              return (
                <Fragment key={key}>
                  <tr className={`clickable${isOpen ? " selected" : ""}`} onClick={() => setOpen(isOpen ? null : key)}
                    aria-expanded={isOpen} tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setOpen(isOpen ? null : key)}>
                    <td>
                      <div className="ticker">{c.ticker}{c.approx && <span className="approx" title="Part of this uses fund weights or an index proxy">≈</span>}</div>
                      <div className="name">{c.name}</div>
                    </td>
                    <td className="hide-narrow"><SplitBar c={c} max={max} /></td>
                    <td className="num"><Amount value={c.direct_value} /></td>
                    <td className="num hide-narrow"><Amount value={c.via_fund_value} /></td>
                    <td className="num"><Amount value={c.total} /></td>
                    <td className="num">{fmtPct(c.total / summary.total_value)}</td>
                  </tr>
                  {isOpen && c.ticker && (
                    <tr><td colSpan={6}><Detail ticker={c.ticker} asOf={asOf} slice={slice} /></td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        <div className="legend">
          <span><i className="swatch" style={{ background: "var(--direct)" }} /> Held directly</span>
          <span><i className="swatch" style={{ background: "var(--lens-glass)", boxShadow: "inset 0 0 0 1px var(--lens)" }} /> Through funds</span>
          <span>≈ uses fund weights or an index proxy</span>
        </div>
      </section>
    </>
  );
}
