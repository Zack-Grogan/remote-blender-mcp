"""Own Blender's process, enforce job deadlines and shut down idle sessions."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from common import JOBS, STATE, control_lock, read_json, begin_stop, write_json


def main():
    JOBS.mkdir(parents=True, exist_ok=True)
    touch = STATE / "touch"
    touch.touch()
    with control_lock():
        write_json(STATE / "control.json", {"accepting": True})
    idle_seconds = int(os.environ.get("RBM_IDLE_SECONDS", "900"))
    idle_job = None
    log = (STATE / "worker.log").open("a", buffering=1)
    worker = None
    display = None
    if os.environ.get("RBM_MODE") == "gui":
        os.environ.update(DISPLAY=":99", LIBGL_ALWAYS_SOFTWARE="1")
        display = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x800x24", "-nolisten", "tcp"], stdout=log, stderr=log
        )
        time.sleep(1)
    try:
        while True:
            if worker is None or worker.poll() is not None:
                with control_lock():
                    for path in JOBS.glob("*.json"):
                        job = read_json(path)
                        if job["status"] in ("queued", "running"):
                            job.update(
                                status="failed",
                                error="Blender restarted; resubmit after inspecting restored state",
                                partial_change_possible=True,
                                finished_at=time.time(),
                            )
                            write_json(path, job)
                    write_json(STATE / "control.json", {"accepting": True})
                write_json(STATE / "worker.json", {"status": "starting"})
                script = str(Path(__file__).with_name("worker.py"))
                command = (
                    [
                        "/opt/blender/blender",
                        "--disable-autoexec",
                        "--python-use-system-env",
                        "--python",
                        script,
                    ]
                    if display
                    else [sys.executable, script]
                )
                worker = subprocess.Popen(
                    command,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)},
                )
                time.sleep(1)
                if worker.poll() is not None:
                    raise RuntimeError("Blender failed to start; inspect .rbm/worker.log")
            active = False
            with control_lock():
                for path in JOBS.glob("*.json"):
                    job = read_json(path)
                    if job["status"] in ("queued", "running"):
                        active = True
                    if job["status"] == "running" and (
                        path.with_suffix(".cancel").exists()
                        or time.time() - job["started_at"] > job["timeout"]
                    ):
                        # The lock excludes worker completion and claiming the next job.
                        if worker.poll() is None:
                            os.killpg(worker.pid, signal.SIGKILL)
                        worker.wait()
                        job.update(
                            status="cancelled" if path.with_suffix(".cancel").exists() else "timed_out",
                            error="Blender stopped; restoring checkpoint. Unsaved edits lost; queued jobs will fail.",
                            partial_change_possible=True,
                            finished_at=time.time(),
                        )
                        write_json(path, job)
                        write_json(STATE / "worker.json", {"status": "recovering"})
                        break
            if idle_job:
                job = read_json(JOBS / f"{idle_job}.json")
                if job["status"] == "succeeded":
                    subprocess.run(["sync", str(STATE)], check=True)
                    return
                if job["status"] not in ("queued", "running"):
                    idle_job = None
                    touch.touch()
            elif not active and time.time() - touch.stat().st_mtime > idle_seconds:
                # Recheck activity and pending work under the same lock as submission.
                with control_lock():
                    quiet = time.time() - touch.stat().st_mtime > idle_seconds and not any(
                        read_json(p)["status"] in ("queued", "running") for p in JOBS.glob("*.json")
                    )
                    if quiet:
                        control = read_json(STATE / "control.json", {})
                        write_json(STATE / "control.json", {**control, "accepting": False})
                if quiet:
                    idle_job = begin_stop()["id"]
            time.sleep(0.2)
    except BaseException as exc:
        with (STATE / "worker.log").open("rb") as error_log:
            error_log.seek(max(0, (STATE / "worker.log").stat().st_size - 16000))
            tail = error_log.read().decode("utf-8", errors="replace")
        write_json(STATE / "worker.json", {"status": "failed", "error": str(exc), "log_tail": tail})
        raise
    finally:
        if worker and worker.poll() is None:
            os.killpg(worker.pid, signal.SIGKILL)
            worker.wait()
        if display:
            display.terminate()
            display.wait()
        status = read_json(STATE / "worker.json", {})
        write_json(
            STATE / "worker.json", {**status, "status": "stopped" if not status.get("error") else "failed"}
        )
        subprocess.run(["sync", str(STATE)], check=False)
        log.close()


if __name__ == "__main__":
    main()
