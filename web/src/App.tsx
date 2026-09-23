import { useCallback, useEffect, useState } from "react";
import { api, type Meta, type Slice } from "./api";
import { accountTypeLabel } from "./format";
import { Icon } from "./icons";
import ExposureView from "./views/Exposure";
import AccountsView from "./views/Accounts";
import ChecksView from "./views/Checks";
import ImportView from "./views/Import";
import ActivityView from "./views/Activity";
import ChangesView from "./views/Changes";
import InboxView from "./views/Inbox";

const VIEWS = [
  { id: "exposure", label: "What you own", icon: "own" },
  { id: "changes", label: "What changed", icon: "changes" },
  { id: "inbox", label: "Questions", icon: "questions" },
  { id: "accounts", label: "Accounts", icon: "accounts" },
  { id: "import", label: "Import", icon: "import" },
  { id: "checks", label: "Reconcile", icon: "reconcile" },
  { id: "activity", label: "Activity", icon: "activity" },
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
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
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

  const [questions, setQuestions] = useState(0);
  const reload = useCallback(() => {
    api.meta().then((m) => { setMeta(m); setAsOf((d) => d || m.default_as_of); setError(null); })
      .catch((e) => setError(e.message));
    api.inbox().then((i) => setQuestions(i.length)).catch(() => undefined);
  }, []);
  useEffect(reload, [reload]);

  const set = (key: keyof Slice) => (v: string) => setSlice((s) => ({ ...s, [key]: v || undefined }));
  const title = VIEWS.find((v) => v.id === view)!.label;

  if (error && !meta) {
    return <main className="sheet" style={{ margin: 40 }}><p className="error" style={{ margin: 6 }}>{error}</p></main>;
  }
  return (
    <div className={`app${privacy ? " private" : ""}`}>
      <aside className="sidebar glass">
        <div className="brand"><Logo /> Glassfolio</div>
        <nav className="nav" aria-label="Views">
          {VIEWS.map((v) => (
            <button key={v.id} aria-current={view === v.id ? "page" : undefined} onClick={() => setView(v.id)}>
              <Icon name={v.icon} />
              {v.label}
              {v.id === "inbox" && questions > 0 && <span className="badge" aria-label={`${questions} open`}>{questions}</span>}
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
        <header className="toolbar">
          <h1>{title}</h1>
          <div className="capsule-group glass">
            <label className="field">
              <Icon name="calendar" size={16} />
              <span className="hide-narrow">As of</span>
              <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} aria-label="As of date" />
            </label>
            <span className="divider" aria-hidden />
            <button className="icon-btn" aria-pressed={privacy} onClick={() => setPrivacy((p) => !p)}
              aria-label={privacy ? "Show amounts" : "Hide amounts"} title={privacy ? "Show amounts" : "Hide amounts"}>
              <Icon name={privacy ? "eyeOff" : "eye"} />
            </button>
          </div>
        </header>
        {meta && asOf && view === "exposure" && <ExposureView asOf={asOf} slice={slice} onImport={() => setView("import")} />}
        {meta && asOf && view === "changes" && <ChangesView key={asOf} asOf={asOf} slice={slice} statementDates={meta.statement_dates} onInbox={() => setView("inbox")} />}
        {view === "inbox" && <InboxView onChange={reload} />}
        {meta && view === "accounts" && <AccountsView meta={meta} onChange={reload} />}
        {meta && view === "import" && <ImportView meta={meta} asOf={asOf} onDone={reload} />}
        {meta && view === "checks" && <ChecksView meta={meta} asOf={asOf} />}
        {view === "activity" && <ActivityView />}
      </div>
    </div>
  );
}
