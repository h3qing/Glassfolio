import threading
import time

import pytest

from glassfolio.server.jobs import MAX_JOBS, Jobs


def wait(jobs: Jobs, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while jobs.get(job_id).status == "running":
        assert time.monotonic() < deadline, "job didn't finish"
        time.sleep(0.01)
    return jobs.get(job_id)


def test_a_job_reports_its_stage_then_its_result():
    go = threading.Event()

    def work(progress):
        progress("Reading page 1 of 2", 1, 2)
        go.wait(5)
        return "done", {"answer": 42}

    jobs = Jobs()
    job = jobs.start("read", "statement.pdf", work)
    deadline = time.monotonic() + 5
    while jobs.get(job.id).stage != "Reading page 1 of 2":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    running = jobs.get(job.id).summary()
    assert running["status"] == "running" and (running["step"], running["steps"]) == (1, 2)
    assert running["label"] == "statement.pdf" and "result" not in running
    go.set()
    done = wait(jobs, job.id)
    assert done.status == "done" and done.result == {"answer": 42}


def test_expected_errors_are_shown_and_unexpected_ones_are_not():
    jobs = Jobs()
    bad = jobs.start("read", "a", lambda progress: (_ for _ in ()).throw(ValueError("no date found")))
    assert wait(jobs, bad.id).result == {"error": "no date found"}
    boom = jobs.start("read", "b", lambda progress: (_ for _ in ()).throw(RuntimeError("secret 12,345.67")))
    failed = wait(jobs, boom.id)
    assert failed.status == "failed" and "12,345" not in failed.result["error"]
    assert "RuntimeError" in failed.result["error"]


def test_one_job_of_each_kind_at_a_time():
    go = threading.Event()
    jobs = Jobs()
    first = jobs.start("read", "a", lambda progress: (go.wait(5), ("done", {}))[1])
    with pytest.raises(ValueError, match="still reading"):
        jobs.start("read", "b", lambda progress: ("done", {}))
    other = jobs.start("evaluate", "model", lambda progress: ("done", {}))  # a different kind may run
    go.set()
    wait(jobs, first.id)
    wait(jobs, other.id)
    again = jobs.start("read", "c", lambda progress: ("done", {}))
    assert wait(jobs, again.id).status == "done"


def test_a_running_job_cannot_be_forgotten_but_a_finished_one_can():
    go = threading.Event()
    jobs = Jobs()
    job = jobs.start("read", "a", lambda progress: (go.wait(5), ("done", {"token": "up_1"}))[1])
    with pytest.raises(ValueError, match="finish"):
        jobs.forget(job.id)  # else a second read could start beside it
    go.set()
    wait(jobs, job.id)
    assert jobs.forget(job.id).result == {"token": "up_1"}
    assert job.id not in {j.id for j in jobs.all()}
    with pytest.raises(ValueError, match="no longer"):
        jobs.get(job.id)


def test_a_stuck_job_stops_blocking_after_its_time_limit():
    go = threading.Event()
    jobs = Jobs(limits={"read": 0.05})
    stuck = jobs.start("read", "a", lambda progress: (go.wait(5), ("done", {}))[1])
    time.sleep(0.1)
    shown = jobs.get(stuck.id)
    assert shown.status == "failed" and "gave up" in shown.result["error"]
    fresh = jobs.start("read", "b", lambda progress: ("done", {}))
    assert wait(jobs, fresh.id).status == "done"
    go.set()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_job_always_ends_even_on_base_exceptions():
    def work(progress):
        raise KeyboardInterrupt

    jobs = Jobs()
    job = jobs.start("read", "a", work)
    assert wait(jobs, job.id).status == "failed"
    time.sleep(0.05)  # let the interrupt leave the thread inside this test


def test_a_new_job_replaces_finished_ones_of_its_kind():
    jobs = Jobs()
    first = jobs.start("read", "a", lambda progress: ("failed", {"error": "no model"}))
    wait(jobs, first.id)
    test = jobs.start("evaluate", "m", lambda progress: ("done", {}))
    wait(jobs, test.id)
    second = jobs.start("read", "b", lambda progress: ("done", {}))
    wait(jobs, second.id)
    assert [j.id for j in jobs.all()] == [second.id, test.id]  # newest first; the old error is gone


def test_finished_jobs_are_bounded():
    jobs = Jobs()
    ids = []
    for i in range(MAX_JOBS + 3):
        ids.append(jobs.start(f"kind{i}", str(i), lambda progress: ("done", {})).id)
        wait(jobs, ids[-1])
    assert [j.id for j in jobs.all()] == ids[::-1][:MAX_JOBS]
