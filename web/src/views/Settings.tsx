import { useEffect, useRef, useState } from "react";
import { api, type EvalRun, type Job, type ModelInfo } from "../api";
import { JobProgress } from "../jobs";

export default function SettingsView({ job, onJob }: { job?: Job; onJob: (job: Job) => void }) {
  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [url, setUrl] = useState("");
  const [run, setRun] = useState<EvalRun | null>(null);
  const shown = useRef<string | null>(null);  // the test whose result is on screen
  const [error, setError] = useState<string | null>(null);
  const load = () => api.models().then((m) => { setInfo(m); setUrl(m.url); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  useEffect(() => {  // a finished test shows up here, even if you left while it ran
    if (!job || job.status === "running" || job.id === shown.current) return;
    shown.current = job.id;
    api.job<EvalRun>(job.id).then((j) => {
      if ("error" in j.result) setError(j.result.error);
      else setRun(j.result);
      load();
    }).catch((e) => setError(e.message));
  }, [job]);
  const testing = job?.status === "running";
  if (!info) return <section className="sheet"><p className="muted">Loading…</p></section>;

  const choose = (name: string | null) =>
    api.chooseModel(url, name).then(() => { setRun(null); setError(null); load(); }).catch((e) => setError(e.message));
  const score = info.name ? info.evals[info.name] : undefined;
  const result = run?.model === info.name ? run : null;  // a test of another model isn't this one's
  return (
    <>
      <section className="sheet">
        <h2 className="section-title">Local model</h2>
        <p className="muted" style={{ margin: "0 6px 14px", maxWidth: "70ch" }}>
          Glassfolio uses a model running on this Mac to read your exports. Any OpenAI-compatible local
          server works: Ollama, LM Studio, llama.cpp, an MLX server. Your data never goes to a
          server on another machine; Glassfolio refuses to connect to one.
        </p>
        <div className="row">
          <label className="field" style={{ minWidth: 300 }}>Server address
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://127.0.0.1:11434/v1" />
          </label>
          <button className="btn" onClick={() => choose(info.name)}>Connect</button>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <label className="field">Model
            <select className="select" value={info.name ?? ""} onChange={(e) => choose(e.target.value || null)}>
              <option value="">None (built-in rules only)</option>
              {info.available.map((m) => <option key={m} value={m}>{m}{info.evals[m] ? ` (${info.evals[m].passed}/${info.evals[m].total})` : ""}</option>)}
              {info.name && !info.available.includes(info.name) && <option value={info.name}>{info.name} (not found)</option>}
            </select>
          </label>
          <button className="btn primary" disabled={!info.name || testing} onClick={() => {
            setError(null); setRun(null);
            api.evaluate().then((r) => onJob(r.job)).catch((e) => setError(e.message));
          }}>{testing ? "Testing…" : "Test this model"}</button>
        </div>
        {testing && <JobProgress job={job} align="left" title={`Testing ${job.label}`}
          stage={job.steps ? `File ${job.step} of ${job.steps}: ${job.stage}` : job.stage}
          note="You can leave this page; the test keeps running. A strong model takes a few minutes." />}
        {!info.reachable && <p className="status warn" style={{ margin: "12px 6px 0" }}>! No local model server answered at this address. Start Ollama or LM Studio, or leave the model on None.</p>}
        {score && !result && <p className="muted" style={{ margin: "12px 6px 0" }}>Last test: {score.passed} of {score.total} sample files read correctly in {score.seconds}s.</p>}
        <p className="faint" style={{ margin: "12px 6px 0", fontSize: 13 }}>Models with 8B parameters or more work best. Very small models (3B) usually fail the test; Glassfolio then falls back to its built-in rules, which you can correct by hand.</p>
        {error && <p className="error">{error}</p>}
      </section>
      {document.documentElement.dataset.shell === "tauri" && (
        <section className="sheet">
          <h2 className="section-title">Unlocking</h2>
          <label className="check" style={{ margin: "0 6px" }}>
            <input type="checkbox" checked={info.require_touch_id}
              onChange={(e) => api.setTouchId(e.target.checked).then(load).catch((x) => setError(x.message))} />
            Ask for Touch ID (or your Mac's password) when Glassfolio opens
          </label>
          <p className="faint" style={{ margin: "10px 6px 0", fontSize: 13 }}>Your data stays encrypted either way; this adds a check before the app reads its key from the Keychain.</p>
        </section>
      )}
      {result && (
        <section className="sheet">
          <h2 className="section-title">{result.passed === result.total ? "✓" : "!"} {result.model}: {result.passed} of {result.total} sample files read correctly</h2>
          <table>
            <tbody>
              {result.results.map((r) => (
                <tr key={r.file}>
                  <td style={{ width: 90 }}><span className={`status ${r.passed ? "pass" : "fail"}`}>{r.passed ? "✓ Pass" : "✗ Fail"}</span></td>
                  <td>{r.file.replace(/_/g, " ").replace(".csv", "")}{r.problems.length > 0 && <div className="muted" style={{ fontSize: 13 }}>{r.problems.slice(0, 2).join("; ")}</div>}</td>
                  <td className="num faint">{r.seconds}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
