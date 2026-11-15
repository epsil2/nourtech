"""Bounded background jobs for control-plane operations.

Control-plane principle 11: long work (Jira materialisation, Hermes dispatch,
deployment requests) must not block the browser.  The host has 1 vCPU and no
message broker, so instead of adding Celery/Redis Streams we run one thread per
job inside the API process and persist its state in ``cp_documents`` so the UI
can poll ``GET /api/v1/jobs/{id}``.

Guarantees:
* a job is persisted *before* the thread starts, so a crash leaves a visible
  ``running`` row instead of a silent loss;
* ``reap_orphans`` marks jobs left ``running`` by a restart as ``failed`` with an
  explicit reason instead of pretending they are still in progress;
* the number of concurrent jobs is capped (``CP_MAX_JOBS``, default 2) so a
  burst cannot exhaust the box.
"""

from __future__ import annotations

import os
import threading
import traceback
import uuid
from typing import Any, Callable

KIND = "job"
_MAX = max(1, min(int(os.environ.get("CP_MAX_JOBS", "2") or 2), 8))
_slots = threading.BoundedSemaphore(_MAX)


def submit(
    store,
    *,
    kind: str,
    subject_type: str,
    subject_id: str,
    fn: Callable[[], Any],
    project_id: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    job = store.cp_save(
        KIND,
        {
            "kind": kind,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "status": "queued",
            "actor": actor,
            "result": None,
            "error": None,
            "attempts": 0,
        },
        project_id=project_id,
    )
    if os.environ.get("CP_JOBS_SYNC", "").strip().lower() in {"1", "true", "yes"}:
        _execute(store, job["id"], fn)
        return store.cp_get(KIND, job["id"]) or job

    def runner() -> None:
        with _slots:
            _execute(store, job["id"], fn)

    threading.Thread(target=runner, name=f"cp-job-{job['id'][:8]}", daemon=True).start()
    return job


def _execute(store, job_id: str, fn: Callable[[], Any]) -> None:
    store.cp_patch(KIND, job_id, {"status": "running", "attempts": 1})
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - the failure is the payload
        store.cp_patch(
            KIND,
            job_id,
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                "trace": traceback.format_exc(limit=3)[-1500:],
            },
        )
        return
    store.cp_patch(KIND, job_id, {"status": "succeeded", "result": result})


def reap_orphans(store, reason: str = "api restarted while the job was running") -> int:
    """Mark jobs left in running/queued state by a previous process as failed."""
    marked = 0
    for status in ("running", "queued"):
        for job in store.cp_list(KIND, status=status, limit=200):
            store.cp_patch(KIND, job["id"], {"status": "failed", "error": reason})
            marked += 1
    return marked


def new_key(prefix: str = "") -> str:
    token = uuid.uuid4().hex[:12]
    return f"{prefix}{token}" if prefix else token
