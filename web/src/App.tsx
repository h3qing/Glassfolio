import { useCallback, useEffect, useState } from "react";
import { api, type Meta, type Slice } from "./api";
import { accountTypeLabel } from "./format";
import ExposureView from "./views/Exposure";
import AccountsView from "./views/Accounts";
import ChecksView from "./views/Checks";
import ImportView from "./views/Import";
import ActivityView from "./views/Activity";

const VIEWS = [
  { id: "exposure", label: "What you own" },
  { id: "accounts", label: "Accounts" },
  { id: "import", label: "Import" },
  { id: "checks", label: "Reconcile" },
  { id: "activity", label: "Activity" },
] as const;
type ViewId = (typeof VIEWS)[number]["id"];

function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
      <rect x="3" y="9" width="18" height="18" rx="4" fill="var(--direct)" />
      <rect x="8" y="6" width="18" height="18" rx="4" fill="var(--lens)" fillOpacity=".5" stroke="#fff" strokeOpacity=".6" />
      <rect x="13" y="3" width="16" height="16" rx="4" fill="#9FD3DA" fillOpacity=".4" stroke="#fff" strokeOpacity=".8" />
    </svg>
  );
}

function Select({ label, value, options, onChange }: {
  label: string; value: string; options: [string, string][]; onChange: (v: string) => void;
}) {
  return (
    <label className="field">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Everyone / all</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewId>("exposure");
  const [slice, setSlice] = useState<Slice>({});
  const [asOf, setAsOf] = useState("");
  const [privacy, setPrivacy] = useState(false);

  const reload = useCallback(() => {
    api.meta().then((m) => { setMeta(m); setAsOf((d) => d || m.default_as_of); setError(null); })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(reload, [reload]);

  const set = (key: keyof Slice) => (v: string) => setSlice((s) => ({ ...s, [key]: v || undefined }));
  const title = VIEWS.find((v) => v.id === view)!.label;

  if (error && !meta) {
    return <main className="sheet" style={{ margin: 40 }}><p className="error">{error}</p></main>;
  }
  return (
    <div className={`app${privacy ? " private" : ""}`}>
      <aside className="sidebar glass">
        <div className="brand"><Logo /> Glassfolio</div>
        <nav className="nav" aria-label="Views">
          {VIEWS.map((v) => (
            <button key={v.id} aria-current={view === v.id ? "page" : undefined} onClick={() => setView(v.id)}>
              {v.label}
            </button>
          ))}
        </nav>
        {meta && (
          <div className="filters">
            <h2>Show</h2>
            <Select label="Person" value={slice.owner ?? ""} onChange={set("owner")}
              options={meta.owners.map((o) => [o, o])} />
            <Select label="Account type" value={slice.account_type ?? ""} onChange={set("account_type")}
              options={meta.account_types.map((t) => [t, accountTypeLabel[t] ?? t])} />
            <Select label="Broker" value={slice.broker ?? ""} onChange={set("broker")}
              options={meta.brokers.map((b) => [b, b])} />
            <Select label="Account" value={slice.account ?? ""} onChange={set("account")}
              options={meta.accounts.map((a) => [a.nickname, a.nickname])} />
          </div>
        )}
      </aside>
      <div className="main">
        <header className="toolbar glass">
          <h1>{title}</h1>
          <label className="field">
            As of
            <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </label>
          <button className="btn" aria-pressed={privacy} onClick={() => setPrivacy((p) => !p)}>
            {privacy ? "Show amounts" : "Hide amounts"}
          </button>
        </header>
        {meta && asOf && view === "exposure" && <ExposureView asOf={asOf} slice={slice} onImport={() => setView("import")} />}
        {meta && view === "accounts" && <AccountsView meta={meta} onChange={reload} />}
        {meta && view === "import" && <ImportView meta={meta} asOf={asOf} onDone={reload} />}
        {meta && view === "checks" && <ChecksView meta={meta} asOf={asOf} />}
        {view === "activity" && <ActivityView />}
      </div>
    </div>
  );
}
