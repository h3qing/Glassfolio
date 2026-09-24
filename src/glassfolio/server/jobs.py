"""Slow work (reading a document, testing a model) runs as a job the page can follow.

A job runs in a worker thread and reports its stage; the page polls. Jobs live in this
process's memory, so progress and results survive switching views or reloading the
page, not a restart. One job of each kind runs at a time.
"""

import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

import duckdb

from glassfolio.lake import new_id
from glassfolio.llm import ModelError
from glassfolio.parsing import printable

MAX_JOBS = 8
BUSY = {"read": "still reading the last file; wait for it to finish",
        "evaluate": "a model test is already running"}
# A job running longer than this stops blocking its kind (a stalled model server, a PDF
# that never finishes parsing); if it ends later, its result still shows up.
LIMITS = {"read": 15 * 60, "evaluate": 60 * 60}

Progress = Callable[..., None]  # progress(stage, step=0, steps=0)
Work = Callable[[Progress], tuple[str, dict]]  # returns ("done" | "failed", result)


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    label: str
    started: float
    stage: str = "Starting"
    step: int = 0
    steps: int = 0  # 0: no count known, the page shows an indeterminate bar
    status: str = "running"  # running | done | failed
    result: dict | None = None
    ended: float | None = None

    def summary(self, now: float | None = None) -> dict:
        end = self.ended or (time.monotonic() if now is None else now)
        return {"id": self.id, "kind": self.kind, "label": self.label, "stage": self.stage,
                "step": self.step, "steps": self.steps, "status": self.status,
                "seconds": round(end - self.started)}


def _given_up(job: Job, limit: float, now: float) -> Job:
    if job.status != "running" or now - job.started <= limit:
        return job
    return replace(job, status="failed", ended=now,
                   result={"error": f"gave up waiting after {round(limit / 60)} minutes; try again"})


def _bounded(jobs: dict[str, Job]) -> dict[str, Job]:
    """Every running job, plus the newest finished ones up to MAX_JOBS in all."""
    finished = [k for k, j in jobs.items() if j.status != "running"]
    drop = set(finished[:max(0, len(jobs) - MAX_JOBS)])
    return {k: j for k, j in jobs.items() if k not in drop}


class Jobs:
    def __init__(self, limits: dict[str, float] = LIMITS):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._limits = limits

    def _sweep(self) -> None:
        """Mark jobs past their time limit as failed. Call with the lock held."""
        now = time.monotonic()
        self._jobs = {k: _given_up(j, self._limits.get(j.kind, 3600), now) for k, j in self._jobs.items()}

    def start(self, kind: str, label: str, work: Work) -> Job:
        with self._lock:
            self._sweep()
            if any(j.kind == kind and j.status == "running" for j in self._jobs.values()):
                raise ValueError(BUSY.get(kind, f"a {kind} job is already running"))
            job = Job(new_id("job"), kind, label, time.monotonic())
            # the page shows only the newest of a kind; an older result must not resurface
            kept = {k: j for k, j in self._jobs.items() if j.kind != kind or j.status == "running"}
            self._jobs = _bounded({**kept, job.id: job})
        threading.Thread(target=self._run, args=(job.id, work), daemon=True).start()
        return job

    def _run(self, job_id: str, work: Work) -> None:
        def progress(stage: str, step: int = 0, steps: int = 0) -> None:
            self._update(job_id, stage=stage, step=step, steps=steps)

        status, result = "failed", {"error": "stopped unexpectedly"}
        try:
            status, result = work(progress)
        except (ValueError, KeyError, duckdb.Error, ModelError) as exc:
            status, result = "failed", {"error": printable(str(exc))}
        except Exception as exc:  # never shown: the message may quote the file
            status, result = "failed", {"error": f"something went wrong ({type(exc).__name__})"}
        finally:  # a job always ends, so it can't block its kind forever
            self._update(job_id, status=status, result=result, ended=time.monotonic())

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:  # a forgotten job stays forgotten
                self._jobs = _bounded({**self._jobs, job_id: replace(job, **changes)})

    def get(self, job_id: str) -> Job:
        with self._lock:
            self._sweep()
            job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("this job is no longer available")
        return job

    def all(self) -> tuple[Job, ...]:
        """Newest first."""
        with self._lock:
            self._sweep()
            return tuple(reversed(self._jobs.values()))

    def forget(self, job_id: str) -> Job | None:
        """Drop a finished job; returns it, so its caller can drop what it points to."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == "running":
                raise ValueError("wait for it to finish")
            self._jobs = {k: j for k, j in self._jobs.items() if k != job_id}
            return job
