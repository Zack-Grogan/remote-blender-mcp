"""Resource-only live test; agent delegation remains with the calling harness.

--prepare opens two isolated sessions and leaves them for assigned test workers.
--check verifies their finished artifacts and cleanup, then checks workspace recovery.
"""

import argparse
import asyncio
import json
import hashlib
import re
import sys
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OUT = Path(".validation/parallel-workspaces").resolve()
STATE = OUT / "assignments.json"


def store(value):
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(value, indent=2) + "\n")


async def main(prepare):
    params = StdioServerParameters(command=sys.executable, args=["-m", "remote_blender_mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def call(name, **args):
                r = await client.call_tool(name, args)
                if r.isError:
                    raise RuntimeError(str(r.content))
                return r.structuredContent or json.loads(r.content[0].text)

            async def job(sid, code):
                value = await call("execute_python", session_id=sid, code=code, checkpoint=False)
                while value["status"] in ("queued", "running"):
                    value = await call("job_status", session_id=sid, job_id=value["id"], wait_seconds=30)
                assert value["status"] == "succeeded", value
                return value

            if prepare:
                record = {"project": f"parallel-{uuid.uuid4().hex[:12]}", "sessions": [], "checks": {}}
                store(record)

                async def create():
                    session = await call(
                        "create_session",
                        project=record["project"],
                        new_workspace=True,
                        lifetime_seconds=1800,
                        idle_seconds=300,
                    )
                    record["sessions"].append(session)
                    store(record)
                    return session

                results = await asyncio.gather(create(), create(), return_exceptions=True)
                if any(isinstance(r, Exception) for r in results):
                    for s in record["sessions"]:
                        await call("stop_session", session_id=s["session_id"], discard_unsaved=True)
                    raise RuntimeError(str(results))
                assert len({s["workspace_id"] for s in results}) == 2
                assert len({s["sandbox_id"] for s in results}) == 2
                assert len({s["volume"] for s in results}) == 2
                assert all(s["project"] == record["project"] for s in results)
                record["checks"]["separate_sandbox_and_volume_same_project"] = True
                record["sessions"] = results
                for s, color in zip(results, ["red", "blue"]):
                    s["item"] = color
                    s["output_dir"] = str(OUT / s["workspace_id"])
                store(record)
                try:
                    jobs = await asyncio.gather(
                        *(
                            job(
                                s["session_id"],
                                f"""
import time
from pathlib import Path
started = time.time()
bpy.context.scene['worker_marker'] = '{s["item"]}'
Path('/workspace/same-name.txt').write_text('{s["item"]}')
time.sleep(8)
result = {{'marker': bpy.context.scene['worker_marker'], 'started': started, 'finished': time.time()}}
""",
                            )
                            for s in results
                        )
                    )
                    times = [j["result"]["result"] for j in jobs]
                    assert max(t["started"] for t in times) < min(t["finished"] for t in times)
                    record["checks"]["overlapping_remote_jobs"] = True
                    foreign = await client.call_tool(
                        "job_status", {"session_id": results[1]["session_id"], "job_id": jobs[0]["id"]}
                    )
                    assert foreign.isError
                    record["checks"]["foreign_job_id_rejected"] = True
                    record["overlap"] = times
                    for s in results:
                        path = Path(s["output_dir"]) / "same-name.txt"
                        await call(
                            "download_file",
                            session_id=s["session_id"],
                            remote_path="same-name.txt",
                            local_path=str(path),
                        )
                        assert path.read_text() == s["item"]
                    record["checks"]["identical_remote_filenames_isolated"] = True
                    store(record)
                    print(json.dumps(record, indent=2), flush=True)
                except BaseException:
                    for s in results:
                        await call("stop_session", session_id=s["session_id"], discard_unsaved=True)
                    raise
            else:
                record = json.loads(STATE.read_text())
                for s in record["sessions"]:
                    sid = s["session_id"]
                    status = await call("session_status", session_id=sid)
                    assert status["status"] == "ended", status
                    root = Path(s["output_dir"])
                    assert (root / "render.png").is_file() and (root / "scene.blend").is_file()
                    assert (root / "worker-result.json").is_file()
                    worker = json.loads((root / "worker-result.json").read_text())
                    assert worker["status"] == "passed" and worker["item"] == s["item"], worker
                    assert worker["session_id"] == sid and worker["workspace_id"] == s["workspace_id"]
                    hashes = set(re.findall(r"[0-9a-f]{64}", json.dumps(worker)))
                    for filename in ["render.png", "scene.blend"]:
                        with (root / filename).open("rb") as data:
                            assert hashlib.file_digest(data, "sha256").hexdigest() in hashes

                record["checks"]["both_agent_deliveries_and_cleanup"] = True
                first, second = record["sessions"]
                created = []
                try:
                    for original in [first, second]:
                        reopened = await call(
                            "create_session",
                            project=record["project"],
                            workspace_id=original["workspace_id"],
                            lifetime_seconds=600,
                            idle_seconds=180,
                        )
                        created.append(reopened)
                        assert reopened["volume"] == original["volume"]
                        inspected = await job(
                            reopened["session_id"], "result = bpy.context.scene['worker_marker']"
                        )
                        assert inspected["result"]["result"] == original["item"]
                    record["checks"]["independent_workspace_recovery"] = True
                    await call("stop_session", session_id=created[0]["session_id"])
                    inspected = await job(
                        created[1]["session_id"], "result = bpy.context.scene['worker_marker']"
                    )
                    assert inspected["result"]["result"] == second["item"]
                    record["checks"]["stopping_one_does_not_stop_sibling"] = True
                finally:
                    for s in created:
                        await call("stop_session", session_id=s["session_id"], discard_unsaved=True)
                record["complete"] = True
                store(record)
                print(json.dumps(record["checks"], indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    asyncio.run(main(parser.parse_args().prepare))
