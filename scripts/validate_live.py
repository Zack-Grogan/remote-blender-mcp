"""Run billable, live Modal acceptance checks through a real stdio MCP client."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OUT = Path(".validation").resolve()
OUT.mkdir(exist_ok=True)
REPORT = []


def record(name, value):
    REPORT.append({"check": name, "value": value, "time": time.time()})
    (OUT / os.environ.get("RBM_TEST_REPORT", "live-report.json")).write_text(json.dumps(REPORT, indent=2))
    print(name, json.dumps(value)[:400], flush=True)


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "remote_blender_mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def call(name, **args):
                result = await client.call_tool(name, args)
                if result.isError:
                    raise RuntimeError(str(result.content))
                if result.structuredContent is not None:
                    return result.structuredContent
                return json.loads(result.content[0].text)

            async def job(name, **args):
                value = await call(name, **args)
                if value.get("status") in ("queued", "running"):
                    while value["status"] in ("queued", "running"):
                        value = await call(
                            "job_status", session_id=args["session_id"], job_id=value["id"], wait_seconds=30
                        )
                return value

            session_id = os.environ.get("RBM_TEST_SESSION")
            if not session_id:
                value = await call(
                    "create_session",
                    project=os.environ.get("RBM_TEST_PROJECT", "prototype-validation"),
                    gpu=os.environ.get("RBM_TEST_GPU"),
                    lifetime_seconds=1800,
                    idle_seconds=300,
                )
                session_id = value["session_id"]
                record("create", value)
            args = {"session_id": session_id}
            try:
                value = await job("inspect_scene", **args)
                assert value["status"] == "succeeded", value
                record("inspect", value["result"])
                value = await job(
                    "execute_python",
                    **args,
                    code="""
if 'PrototypeSphere' not in bpy.data.objects:
    bpy.ops.mesh.primitive_uv_sphere_add(location=(1.5, 0, 1))
    bpy.context.object.name = 'PrototypeSphere'
result = {'name': bpy.data.objects['PrototypeSphere'].name, 'version': bpy.app.version_string}
""",
                )
                assert value["status"] == "succeeded", value
                revision = value["result"]["checkpoint"]["revision"]
                record("edit_checkpoint", value["result"])
                value = await job(
                    "render",
                    **args,
                    width=256,
                    height=256,
                    samples=4,
                    device="CUDA" if os.environ.get("RBM_TEST_GPU") else "CPU",
                )
                assert value["status"] == "succeeded", value
                record("render", value["result"])
                image = await client.call_tool("view_image", {**args, "path": value["result"]["path"]})
                assert not image.isError and any(c.type == "image" for c in image.content)
                record("inline_mcp_image", True)
                stamp = str(int(time.time()))
                local_png = OUT / f"{stamp}-render.png"
                value = await call(
                    "download_file", **args, remote_path=value["result"]["path"], local_path=str(local_png)
                )
                record("download_png", value)
                value = await call(
                    "upload_file", **args, local_path=str(local_png), remote_path=f"inputs/{stamp}.png"
                )
                record("upload_png", value)
                value = await job(
                    "execute_python",
                    **args,
                    code=f"""
image = bpy.data.images.load('/workspace/inputs/{stamp}.png', check_existing=True)
image.pack()
image.use_fake_user = True
result = {{'packed_images': len([i for i in bpy.data.images if i.packed_file])}}
""",
                )
                assert value["status"] == "succeeded", value
                record("packed_dependency", value["result"])
                value = await job("save_project", **args, path=f"exports/{stamp}.blend")
                assert value["status"] == "succeeded", value
                value = await call(
                    "download_file",
                    **args,
                    remote_path=f"exports/{stamp}.blend",
                    local_path=str(OUT / f"{stamp}.blend"),
                )
                record("download_blend", value)
                uploaded = await call(
                    "upload_file", **args, local_path=value["path"], remote_path=f"inputs/{stamp}.blend"
                )
                reopened = await job("open_project", **args, path=uploaded["path"])
                assert reopened["status"] == "succeeded", reopened
                record("reopen_uploaded_project", reopened["result"])
                readback = await job(
                    "execute_python",
                    **args,
                    checkpoint=False,
                    code="result = {'packed': len([i for i in bpy.data.images if i.packed_file]), 'sphere': 'PrototypeSphere' in bpy.data.objects}",
                )
                assert readback["result"]["result"]["packed"] >= 1, readback
                assert readback["result"]["result"]["sphere"], readback
                record("dependency_readback", readback["result"]["result"])

                checkpoints = await call("list_checkpoints", **args)
                if isinstance(checkpoints, dict):
                    checkpoints = checkpoints.get("result", checkpoints)
                record("checkpoint_listing", bool(checkpoints))

                value = await job(
                    "execute_python",
                    **args,
                    code="bpy.data.objects['PrototypeSphere'].location.x = 99\nraise ValueError('intentional validation failure')",
                )
                assert value["status"] == "failed" and value["partial_change_possible"]
                record("partial_failure_traceback", "intentional validation failure" in value["error"])
                value = await call(
                    "execute_python", **args, code="import time; time.sleep(120)", timeout_seconds=180
                )
                for _ in range(20):
                    value = await call("job_status", **args, job_id=value["id"], wait_seconds=0)
                    if value["status"] == "running":
                        break
                    await asyncio.sleep(0.2)
                assert value["status"] == "running"
                await call("cancel_job", **args, job_id=value["id"])
                value = await call("job_status", **args, job_id=value["id"], wait_seconds=30)
                assert value["status"] == "cancelled", value
                record("cancel_running", value["status"])
                value = await job(
                    "execute_python",
                    **args,
                    code="result = {'x': bpy.data.objects['PrototypeSphere'].location.x}",
                    checkpoint=False,
                )
                assert value["status"] == "succeeded" and value["result"]["result"]["x"] != 99, value
                record("recovered_saved_scene", value["result"])
                value = await job("restore_checkpoint", **args, revision=revision)
                assert value["status"] == "succeeded", value
                record("restore_revision", value["result"])
                value = await job(
                    "execute_python",
                    **args,
                    code="import time; time.sleep(20)",
                    timeout_seconds=1,
                    checkpoint=False,
                )
                assert value["status"] == "timed_out", value
                record("deadline_recovery", value["status"])
                value = await job("inspect_scene", **args)
                assert value["status"] == "succeeded", value
                record("ready_after_deadline", value["result"]["blender"])

            finally:
                try:
                    record("stop", await call("stop_session", **args))
                except Exception as exc:
                    record("stop_error", str(exc))
                    raise


if __name__ == "__main__":
    asyncio.run(main())
