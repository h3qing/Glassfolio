// Slow work (reading a file, testing a model) runs as a job on the local service, so it
// survives switching pages and reloads. The page polls while anything runs.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Job } from "./api";

export const elapsed = (s: number) => (s < 60 ? `${s} s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);

/** `unseen`: jobs this page watched run whose result hasn't been looked at yet. */
export function useJobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [unseen, setUnseen] = useState<ReadonlySet<string>>(new Set());
  const started = useRef(0);  // counts jobs started here: a list asked for before one is stale
  const refresh = useCallback(() => {
    const asked = started.current;
    return api.jobs().then((listed) => {
      if (asked !== started.current) return;
      setJobs(listed);
      const running = listed.filter((j) => j.status === "running").map((j) => j.id);
      setUnseen((u) => (running.some((id) => !u.has(id)) ? new Set([...u, ...running]) : u));
    }).catch(() => undefined);
  }, []);
  const track = useCallback((job: Job) => {
    started.current += 1;
    setJobs((js) => [job, ...js.filter((j) => j.kind !== job.kind || j.status === "running")]);
    setUnseen((u) => new Set([...u, job.id]));
  }, []);
  const seen = useCallback((id: string) =>
    setUnseen((u) => (u.has(id) ? new Set([...u].filter((x) => x !== id)) : u)), []);
  const forget = useCallback((id: string) => {
    setJobs((js) => js.filter((j) => j.id !== id));
    api.forgetJob(id).catch(() => undefined);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  const busy = jobs.some((j) => j.status === "running");
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(refresh, 1000);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  const latest = (kind: Job["kind"]) => jobs.find((j) => j.kind === kind);
  return { latest, track, seen, forget, unseen };
}

/** Beside a sidebar item: a ring while its job runs, a dot when it finished while you were away. */
export function JobMark({ job, unseen }: { job?: Job; unseen: boolean }) {
  if (!job) return null;
  if (job.status === "running") {
    const r = 6.5, c = 2 * Math.PI * r;
    const part = job.steps ? Math.max(0.08, (job.step - 1) / job.steps) : 0.28;
    return (
      <svg className={`job-ring${job.steps ? "" : " spinning"}`} width="16" height="16" viewBox="0 0 16 16"
        role="img" aria-label={`${job.stage}, ${elapsed(job.seconds)}`}>
        <circle cx="8" cy="8" r={r} className="job-ring-track" />
        <circle cx="8" cy="8" r={r} className="job-ring-fill" strokeDasharray={`${part * c} ${c}`} />
      </svg>
    );
  }
  if (!unseen) return null;
  return <span className={`job-dot ${job.status}`} role="img"
    aria-label={job.status === "done" ? "Finished" : "Needs your attention"} />;
}

/** What a running job is doing: title, stage, a bar (measured when steps are known), time. */
export function JobProgress({ job, title, stage, note, align = "center" }: {
  job: Job; title: string; stage: string; note: string; align?: "center" | "left";
}) {
  const measured = job.steps > 0;
  const done = measured ? Math.max(0, job.step - 1) : 0;
  return (
    <div className={`job-progress ${align}`}>
      <strong className="job-title" title={title}>{title}</strong>
      <p className="job-stage"><span role="status">{stage}</span> · {elapsed(job.seconds)}</p>
      <div className="bar" role="progressbar" aria-label={title}
        aria-valuemin={measured ? 0 : undefined} aria-valuemax={measured ? job.steps : undefined}
        aria-valuenow={measured ? done : undefined}>
        <i className={measured ? undefined : "sweep"} style={measured ? { width: `${(done / job.steps) * 100}%` } : undefined} />
      </div>
      <p className="job-note">{note}</p>
    </div>
  );
}
