"""Tests for the in-memory background job tracker."""
import threading
import time

from mc_funding_tracker import jobs


def test_start_runs_work_and_stores_done_result():
    result = jobs.start(1, lambda: {"edgar_found": 1})
    assert result is True

    # Wait for the daemon thread to finish (it's near-instant here).
    for _ in range(50):
        if not jobs.is_running(1):
            break
        time.sleep(0.01)

    assert jobs.is_running(1) is False
    popped = jobs.pop_result(1)
    assert popped == {"status": "done", "summary": {"edgar_found": 1}, "error": None}
    # Popping clears it — a second pop returns nothing.
    assert jobs.pop_result(1) is None


def test_start_returns_false_if_already_running():
    release = threading.Event()

    def work():
        release.wait(timeout=2)
        return {}

    assert jobs.start(2, work) is True
    assert jobs.is_running(2) is True
    assert jobs.start(2, lambda: {}) is False  # second start refused

    release.set()
    for _ in range(50):
        if not jobs.is_running(2):
            break
        time.sleep(0.01)
    jobs.pop_result(2)  # clean up


def test_failed_job_stores_error():
    def work():
        raise RuntimeError("boom")

    jobs.start(3, work)
    for _ in range(50):
        if not jobs.is_running(3):
            break
        time.sleep(0.01)

    popped = jobs.pop_result(3)
    assert popped["status"] == "error"
    assert "boom" in popped["error"]


def test_running_ids_reflects_only_in_progress_jobs():
    release = threading.Event()
    jobs.start(4, lambda: (release.wait(timeout=2), {})[1])
    assert 4 in jobs.running_ids()

    release.set()
    for _ in range(50):
        if 4 not in jobs.running_ids():
            break
        time.sleep(0.01)
    assert 4 not in jobs.running_ids()
    jobs.pop_result(4)
