"""Filesystem protocol shared by the controller, supervisor and Blender worker."""

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

PROTOCOL = 2
ROOT = Path(os.environ.get("RBM_WORKSPACE", "/workspace")).resolve()
STATE = ROOT / ".rbm"
JOBS = STATE / "jobs"
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, allow_nan=False)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(data)
    tmp.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


@contextmanager
def control_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def workspace_path(relative):
    if Path(relative).is_absolute():
        raise ValueError("Use a relative workspace path")
    p = (ROOT / relative).resolve()
    if not p.is_relative_to(ROOT) or p == ROOT or p.is_relative_to(STATE):
        raise ValueError("Path must be inside the workspace and outside .rbm")
    return p


def job_path(job_id):
    return JOBS / f"{uuid.UUID(job_id)}.json"


def _submit(kind, params, timeout):
    # Caller holds control_lock, including the stop barrier insertion.
    job_id = str(uuid.uuid4())
    job = dict(id=job_id, kind=kind, params=params, timeout=timeout, status="queued", created_at=time.time())
    write_json(job_path(job_id), job)
    (STATE / "touch").touch()
    return job


def submit(kind, params, timeout=120):
    if not 1 <= timeout <= 3600:
        raise ValueError("Job timeout must be 1–3600 seconds")
    with control_lock():
        if not read_json(STATE / "control.json", {}).get("accepting", True):
            raise ValueError("Session is draining for shutdown; no new jobs accepted")
        return _submit(kind, params, timeout)


def begin_stop():
    with control_lock():
        control = read_json(STATE / "control.json", {})
        previous = read_json(job_path(control["stop_job"])) if control.get("stop_job") else None
        if previous and previous["status"] not in ("failed", "cancelled", "timed_out"):
            return previous
        job = _submit("save", {}, 120)
        write_json(STATE / "control.json", {**control, "accepting": False, "stop_job": job["id"]})
        return job


def claim_next():
    with control_lock():
        jobs = [read_json(p) for p in JOBS.glob("*.json")]
        for job in sorted(jobs, key=lambda j: (j["created_at"], j["id"])):
            if job["status"] == "queued":
                job.update(status="running", started_at=time.time())
                write_json(job_path(job["id"]), job)
                return job
    return None


def finish_job(job):
    with control_lock():
        path = job_path(job["id"])
        current = read_json(path)
        if current["status"] != "running":
            return False
        write_json(path, job)
        control = read_json(STATE / "control.json", {})
        if control.get("stop_job") == job["id"] and job["status"] != "succeeded":
            write_json(STATE / "control.json", {**control, "accepting": True, "stop_job": None})
        return True


def cancel_job(job_id):
    with control_lock():
        path = job_path(job_id)
        job = read_json(path)
        if job is None:
            raise ValueError("Unknown job ID")
        if job["status"] == "queued":
            job.update(status="cancelled", finished_at=time.time())
            write_json(path, job)
            control = read_json(STATE / "control.json", {})
            if control.get("stop_job") == job["id"]:
                write_json(STATE / "control.json", {**control, "accepting": True, "stop_job": None})
        elif job["status"] == "running":
            path.with_suffix(".cancel").touch()
        return dict(id=job["id"], status=job["status"], cancel_requested=job["status"] not in TERMINAL)
