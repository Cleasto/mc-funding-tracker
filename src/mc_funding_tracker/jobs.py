"""In-memory tracker for background research jobs.

Research can take 30-180s (SEC EDGAR + a web-search API call), which is too long
to hold a browser request open — the previous synchronous design caused actual
client-side timeouts. Research now runs on a daemon thread; this module tracks
per-company job state so the dashboard can show "Researching..." and poll until
done, instead of the request blocking until completion.

State lives in memory only (lost on server restart) — acceptable for a local,
single-process, single-user tool. Any rounds already written to the database by
a job in progress are unaffected by a restart; only the "in progress" marker is.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_jobs: Dict[int, Dict[str, Any]] = {}


def start(company_id: int, work: Callable[[], Dict[str, Any]]) -> bool:
    """Start a background research job for a company.

    `work` is a zero-arg callable that runs the research and returns its summary
    dict. Returns False without starting anything if a job is already running
    for this company.
    """
    with _lock:
        if _jobs.get(company_id, {}).get("status") == "running":
            return False
        _jobs[company_id] = {"status": "running", "summary": None, "error": None}

    def _run() -> None:
        try:
            summary = work()
            with _lock:
                _jobs[company_id] = {"status": "done", "summary": summary, "error": None}
        except Exception as e:
            logger.exception(f"Background research job failed for company_id={company_id}")
            with _lock:
                _jobs[company_id] = {"status": "error", "summary": None, "error": str(e)}

    threading.Thread(target=_run, daemon=True).start()
    return True


def is_running(company_id: int) -> bool:
    with _lock:
        return _jobs.get(company_id, {}).get("status") == "running"


def running_ids() -> Set[int]:
    with _lock:
        return {cid for cid, job in _jobs.items() if job["status"] == "running"}


def pop_result(company_id: int) -> Optional[Dict[str, Any]]:
    """Return and clear a finished job's result (status 'done'/'error'), if any.

    Popping (rather than peeking) means the result is surfaced exactly once —
    the next page load after a job finishes shows the outcome, and subsequent
    loads show the normal page with no job in progress.
    """
    with _lock:
        job = _jobs.get(company_id)
        if job is None or job["status"] == "running":
            return None
        return _jobs.pop(company_id)
