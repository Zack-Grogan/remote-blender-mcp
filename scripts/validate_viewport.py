"""Validate GUI behavior through MCP on an existing, caller-owned GUI session."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(session_id):
    report = []
    params = StdioServerParameters(command=sys.executable, args=["-m", "remote_blender_mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def call(name, **args):
                r = await client.call_tool(name, {"session_id": session_id, **args})
                assert not r.isError, r.content
                if name == "viewport_screenshot":
                    assert any(c.type == "image" for c in r.content), "No inline screenshot"
                    return json.loads(next(c.text for c in r.content if c.type == "text"))
                return r.structuredContent or json.loads(r.content[0].text)

            async def job(name, **args):
                r = await call(name, **args)
                while r["status"] in ("queued", "running"):
                    r = await call("job_status", job_id=r["id"], wait_seconds=30)
                return r

            async def check(name, **args):
                r = await job(name, **args)
                assert r["status"] == "succeeded", r
                return r["result"]

            edited = await check(
                "execute_python",
                code="""
if 'ViewportRecoveryProbe' not in bpy.data.objects:
    bpy.ops.mesh.primitive_uv_sphere_add(location=(-3, 0, 0))
    bpy.context.object.name = 'ViewportRecoveryProbe'
result = 'ViewportRecoveryProbe'
""",
            )
            report.append({"check": "gui_edit_checkpoint", "revision": edited["checkpoint"]["revision"]})
            for label, view, shading in [("front", "FRONT", "SOLID"), ("wire", "ISO", "WIREFRAME")]:
                controlled = await check(
                    "control_viewport",
                    view=view,
                    shading=shading,
                    select=["ViewportRecoveryProbe"],
                    frame_selected=True,
                    distance=6,
                    frame=17,
                )
                assert controlled["selected"] == ["ViewportRecoveryProbe"]
                assert controlled["shading"] == shading and controlled["frame"] == 17
                captured = await check("viewport_screenshot")
                assert captured["source"] == "virtual_display"
                target = Path(f".validation/gui-{label}.png").resolve()
                if target.exists():
                    target = target.with_name(f"gui-{label}-{len(report)}.png")
                downloaded = await call("download_file", remote_path=captured["path"], local_path=str(target))
                assert downloaded["size"] > 20000, "Screenshot unexpectedly empty"
                report.append(
                    {
                        "check": label,
                        "control": controlled,
                        "screenshot": captured,
                        "sha256": downloaded["sha256"],
                    }
                )
            assert report[1]["sha256"] != report[2]["sha256"], "Viewport did not visibly change"
            pose = await check("control_viewport", target=[1, 2, 3], rotation=[60, 0, 35], distance=5)
            assert pose["target"] == [1, 2, 3] and abs(pose["distance"] - 5) < 0.01
            report.append({"check": "orbit_pan_zoom", "result": pose})
            hanging = await call("execute_python", code="import time; time.sleep(30)", timeout_seconds=1)
            done = await job("job_status", job_id=hanging["id"], wait_seconds=30)
            assert done["status"] == "timed_out", done
            recovered = await check("control_viewport", view="TOP", shading="SOLID")
            await check("viewport_screenshot")
            report.append({"check": "gui_timeout_recovery", "result": recovered})
            saved = await check("save_project", path=f"exports/gui-{hanging['id']}.blend")
            await check("open_project", path=saved["export_path"])
            await check("control_viewport", view="ISO", shading="SOLID")
            await check("viewport_screenshot")
            report.append({"check": "gui_file_load_timer_survives", "passed": True})
    Path(".validation/viewport-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    asyncio.run(run(parser.parse_args().session_id))
