"""Inject a bad latest-checkpoint checksum in a disposable project, then recover it."""

import json
import time
import uuid
from pathlib import Path

from remote_blender_mcp.controller import Controller

c = Controller()
project = "debug-recovery-" + uuid.uuid4().hex[:10]
owned = []
report = {}
try:
    r = c.create(project, lifetime_seconds=600, idle_seconds=180)
    s = r["session_id"]
    owned.append(s)
    j = c.submit(s, "python", {"code": "bpy.context.scene['recovery_marker'] = 'intact'"})
    saved = c.wait(s, j["id"], 30)
    assert saved["status"] == "succeeded", saved
    revision = saved["result"]["checkpoint"]["revision"]
    sb = c.sandbox(s)
    c.submit(
        s,
        "python",
        {
            "checkpoint": False,
            "code": """
import json, os, subprocess
from pathlib import Path
p = Path('/workspace/.rbm/latest.json')
metadata = json.loads(p.read_text())
metadata['sha256'] = 'intentional-test-mismatch'
p.write_text(json.dumps(metadata))
subprocess.run(['sync', '/workspace'], check=True)
os._exit(7)
""",
        },
    )
    deadline = time.monotonic() + 40
    while sb.poll() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    assert sb.poll() is not None, "Corrupt checkpoint was not refused"
    status = c.rpc(s, "status")
    assert status["status"] == "ended"
    logs = c.rpc(s, "logs")
    assert "checksum mismatch" in logs["worker"]["log_tail"], logs
    checkpoints = c.rpc(s, "checkpoints")
    assert revision in [v["revision"] for v in checkpoints]
    report.update(checksum_failure_refused=True, ended_diagnostics=True, archived_checkpoints=True)
    r = c.create(project, restore_revision=revision, lifetime_seconds=600, idle_seconds=180)
    s = r["session_id"]
    owned.append(s)
    j = c.submit(
        s, "python", {"checkpoint": False, "code": "result = bpy.context.scene.get('recovery_marker')"}
    )
    restored = c.wait(s, j["id"], 30)
    assert restored["result"]["result"] == "intact", restored
    report["explicit_startup_recovery"] = True
    assert c.stop(s)["stopped"]
    report["stopped"] = True
finally:
    for session in owned:
        c.stop(session, discard_unsaved=True)
Path(".validation/recovery-report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
