"""Billable regression checks for queue boundaries, recovery and idle persistence."""

import json
import time
import uuid
from pathlib import Path

from remote_blender_mcp.controller import Controller

c = Controller()
report = []
owned = []
project = f"debug-life-{uuid.uuid4().hex[:10]}"


def record(check, value):
    report.append({"check": check, "value": value})
    Path(".validation/lifecycle-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(check, str(value)[:500], flush=True)


def wait(s, job):
    for _ in range(10):
        result = c.wait(s, job["id"], 30)
        if result["status"] not in ("queued", "running"):
            return result
    raise TimeoutError(job["id"])


def py(s, code, checkpoint=True):
    result = wait(s, c.submit(s, "python", {"code": code, "checkpoint": checkpoint}))
    assert result["status"] == "succeeded", result
    return result["result"]


try:
    r = c.create(project, lifetime_seconds=900, idle_seconds=300)
    s = r["session_id"]
    owned.append(s)
    assert r["worker"]["status"] == "ready"
    record("create_waits_for_ready", True)
    value = py(s, "#" + ("x" * 200000) + "\nresult = 'large request succeeded'", False)
    record("stdin_request_over_argv_limit", value["result"])
    py(s, "bpy.context.scene.frame_set(3)\nresult = bpy.context.scene.render.engine", False)
    rendered = wait(s, c.submit(s, "render", {"frame": 7, "width": 128, "height": 128, "samples": 1}))
    assert rendered["status"] == "succeeded" and rendered["result"]["frame"] == 7, rendered
    value = py(s, "result = bpy.context.scene.frame_current", False)
    assert value["result"] == 3
    record("render_frame_restored", True)
    slow = c.submit(s, "python", {"code": "import time; time.sleep(12)", "checkpoint": False})
    queued = c.submit(
        s, "python", {"code": "bpy.data.objects.new('CancelledMustNotExist', None)", "checkpoint": False}
    )
    cancelled = c.rpc(s, "cancel", job_id=queued["id"])
    assert cancelled["status"] == "cancelled", cancelled
    assert wait(s, slow)["status"] == "succeeded"
    assert not py(s, "result = 'CancelledMustNotExist' in bpy.data.objects", False)["result"]
    record("queued_cancel_never_executes", True)
    slow = c.submit(s, "python", {"code": "import time; time.sleep(60)", "checkpoint": False}, timeout=8)
    queued = c.submit(
        s, "python", {"code": "bpy.data.objects.new('StaleMustNotExist', None)", "checkpoint": False}
    )
    assert wait(s, slow)["status"] == "timed_out"
    assert wait(s, queued)["status"] == "failed"
    assert not py(s, "result = 'StaleMustNotExist' in bpy.data.objects", False)["result"]
    record("restart_rejects_stale_queue", True)
    py(s, "bpy.context.scene['lifecycle_marker'] = 'saved'", True)
    slow = c.submit(s, "python", {"code": "import time; time.sleep(38)", "checkpoint": False})
    stopped = c.stop(s)
    assert not stopped["stopped"] and stopped["draining"], stopped
    try:
        c.submit(s, "python", {"code": "result = 1"})
        raise AssertionError("Shutdown accepted another edit")
    except RuntimeError as exc:
        assert "draining" in str(exc), exc
    final_save = wait(s, {"id": stopped["job_id"]})
    assert final_save["status"] == "succeeded"
    assert c.stop(s)["stopped"]
    assert c.rpc(s, "status")["status"] == "ended"
    assert c.rpc(s, "status", job_id=slow["id"])["status"] == "succeeded"
    record("shutdown_barrier_pending_save_and_ended_status", True)
    r = c.create(project, lifetime_seconds=900, idle_seconds=300)
    s = r["session_id"]
    owned.append(s)
    assert py(s, "result = bpy.context.scene.get('lifecycle_marker')", False)["result"] == "saved"
    c.stop(s)
    record("cross_session_restore", True)
    r = c.create(project + "-idle", lifetime_seconds=180, idle_seconds=60)
    s = r["session_id"]
    owned.append(s)
    py(s, "bpy.context.scene['idle_marker'] = 'persisted'", False)
    sb = c.sandbox(s)
    until = time.monotonic() + 100
    while sb.poll() is None and time.monotonic() < until:
        time.sleep(2)
    assert sb.poll() is not None, "User-idle shutdown failed"
    status = c.rpc(s, "status")
    assert status["checkpoint"] and status["status"] == "ended", status
    record("idle_shutdown_saves_unsaved_scene", True)
    r = c.create(project + "-idle", lifetime_seconds=180, idle_seconds=60)
    s = r["session_id"]
    owned.append(s)
    assert py(s, "result = bpy.context.scene.get('idle_marker')", False)["result"] == "persisted"
    hanging = c.submit(
        s, "python", {"code": "import time; time.sleep(120)", "checkpoint": False}, timeout=180
    )
    assert c.stop(s, discard_unsaved=True)["stopped"]
    assert c.rpc(s, "status", job_id=hanging["id"])["status"] in ("failed", "succeeded")
    record("idle_checkpoint_readback_and_emergency_stop", True)
finally:
    for session in owned:
        try:
            c.stop(session, discard_unsaved=True)
        except Exception as exc:
            print("cleanup", session, str(exc), flush=True)
