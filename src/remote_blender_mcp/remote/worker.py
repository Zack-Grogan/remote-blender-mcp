"""Persistent synchronous Blender interpreter. No Python background threads."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import time
import traceback
import uuid
from pathlib import Path

import bpy
from common import ROOT, STATE, PROTOCOL, claim_next, finish_job, read_json, workspace_path, write_json


class BoundedOutput(io.StringIO):
    def write(self, text):
        remaining = 64000 - self.tell()
        if remaining > 0:
            super().write(text[:remaining])
        return len(text)


def file_hash(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def checkpoint():
    revision = uuid.uuid4().hex
    path = STATE / "checkpoints" / f"{revision}.blend"
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
    record = dict(
        revision=revision,
        path=str(path),
        sha256=file_hash(path),
        saved_at=time.time(),
        blender=bpy.app.version_string,
    )
    write_json(path.with_suffix(".json"), record)
    # Commit project bytes before publishing the checkpoint pointer.
    subprocess.run(["sync", str(ROOT)], check=True)
    write_json(STATE / "latest.json", record)
    subprocess.run(["sync", str(ROOT)], check=True)
    return record


def execute(kind, params):
    if kind in ("environment", "object", "nodes", "dependencies", "api"):
        from inspection import inspect

        return inspect(kind, params)
    if kind in ("viewport", "screenshot"):
        from viewport import control, screenshot

        return control(params) if kind == "viewport" else screenshot()
    if kind == "operator":
        import re
        from contextlib import nullcontext

        name = params["name"].removeprefix("bpy.ops.")
        if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", name):
            raise ValueError("Invalid operator identifier")
        group, op_name = name.split(".")
        op = getattr(getattr(bpy.ops, group), op_name)
        override = nullcontext()
        if params.get("context") == "VIEW_3D":
            from viewport import context

            window, area, region = context()
            override = bpy.context.temp_override(window=window, area=area, region=region)
        with override:
            if not op.poll():
                raise ValueError("Operator poll failed: check selection, mode and editor context")
            status = op("EXEC_DEFAULT", **params.get("properties", {}))
            if "FINISHED" not in status:
                raise ValueError(f"Operator did not finish: {sorted(status)}")
        return dict(
            operator=name,
            status=sorted(status),
            checkpoint=checkpoint() if params.get("checkpoint", True) else None,
        )
    if kind == "python":
        namespace = {"bpy": bpy, "__name__": "__remote_blender__"}
        exec(compile(params["code"], "<mcp>", "exec"), namespace)
        value = namespace.get("result")
        json.dumps(value, allow_nan=False)
        return {"result": value, "checkpoint": checkpoint() if params.get("checkpoint", True) else None}
    if kind == "inspect":
        objects = sorted(bpy.data.objects, key=lambda o: o.name)
        offset, limit = params.get("offset", 0), params.get("limit", 100)
        return dict(
            blender=bpy.app.version_string,
            scene=bpy.context.scene.name,
            frame=bpy.context.scene.frame_current,
            dirty=bpy.data.is_dirty,
            total_objects=len(objects),
            objects=[
                dict(name=o.name, type=o.type, location=list(o.location), dimensions=list(o.dimensions))
                for o in objects[offset : offset + limit]
            ],
        )
    if kind == "save":
        target = workspace_path(params["path"]) if params.get("path") else None
        if target and target.exists():
            raise ValueError("Export path exists; choose a new path")
        saved = checkpoint()
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(
                filepath=str(target), copy=True, relative_remap=True, check_existing=False
            )
            subprocess.run(["sync", str(ROOT)], check=True)
            saved["export_path"] = str(target.relative_to(ROOT))
        return saved
    if kind == "open":
        bpy.ops.wm.open_mainfile(
            filepath=str(workspace_path(params["path"])), load_ui=False, use_scripts=False
        )
        return checkpoint()
    if kind == "restore":
        revision = params["revision"]
        if len(revision) != 32 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("Invalid revision")
        bpy.ops.wm.open_mainfile(
            filepath=str(STATE / "checkpoints" / f"{revision}.blend"), load_ui=False, use_scripts=False
        )
        return checkpoint()
    if kind == "render":
        scene = bpy.context.scene
        if not scene.camera:
            raise ValueError("Scene has no camera; create one with execute_python")
        saved = checkpoint()
        path = workspace_path(f"artifacts/{uuid.uuid4().hex}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        original_frame = scene.frame_current
        original = (
            scene.render.resolution_x,
            scene.render.resolution_y,
            scene.render.resolution_percentage,
            scene.render.filepath,
            scene.render.image_settings.file_format,
            scene.render.engine,
            scene.cycles.samples,
            scene.cycles.device,
        )
        prefs = bpy.context.preferences.addons["cycles"].preferences
        original_compute = prefs.compute_device_type
        original_devices = {d.id: d.use for d in prefs.devices}
        try:
            scene.render.engine = "CYCLES"
            scene.render.resolution_x = params.get("width", 512)
            scene.render.resolution_y = params.get("height", 512)
            scene.render.resolution_percentage = 100
            scene.render.filepath = str(path)
            scene.render.image_settings.file_format = "PNG"
            scene.cycles.samples = params.get("samples", 16)
            device = params.get("device", "CPU")
            devices = []
            if device != "CPU":
                prefs = bpy.context.preferences.addons["cycles"].preferences
                prefs.compute_device_type = device
                prefs.get_devices()
                for d in prefs.devices:
                    d.use = d.type == device
                    if d.use:
                        devices.append(d.name)
                if not devices:
                    raise ValueError(f"No {device} device available; refusing silent CPU fallback")
                scene.cycles.device = "GPU"
            else:
                scene.cycles.device = "CPU"
            if params.get("frame") is not None:
                scene.frame_set(params["frame"])
            bpy.ops.render.render(write_still=True)
            subprocess.run(["sync", str(ROOT)], check=True)
            return dict(
                path=str(path.relative_to(ROOT)),
                revision=saved["revision"],
                device=device,
                devices=devices,
                camera=scene.camera.name,
                frame=scene.frame_current,
                width=scene.render.resolution_x,
                height=scene.render.resolution_y,
                samples=scene.cycles.samples,
            )
        finally:
            prefs.compute_device_type = original_compute
            for d in prefs.devices:
                d.use = original_devices.get(d.id, False)
            scene.frame_set(original_frame)
            (
                scene.render.resolution_x,
                scene.render.resolution_y,
                scene.render.resolution_percentage,
                scene.render.filepath,
                scene.render.image_settings.file_format,
                scene.render.engine,
                scene.cycles.samples,
                scene.cycles.device,
            ) = original
    raise ValueError(f"Unknown operation: {kind}")


def process_next():
    # One job per callback lets GUI redraws run and gives cancellation a clear boundary.
    job = claim_next()
    if job is None:
        return 0.1
    out, err = BoundedOutput(), BoundedOutput()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = execute(job["kind"], job["params"])
            if len(json.dumps(result, allow_nan=False)) > 1_000_000:
                raise ValueError("Result exceeds 1 MB; write large data to a workspace artifact instead")
        job.update(status="succeeded", result=result)
    except BaseException:
        job.update(status="failed", error=traceback.format_exc(), partial_change_possible=True)
    job.update(stdout=out.getvalue(), stderr=err.getvalue(), finished_at=time.time())
    finish_job(job)
    return 0.1


def main():
    latest = read_json(STATE / "latest.json")
    recovery = os.environ.get("RBM_RESTORE_REVISION")
    if recovery:
        import re

        if not re.fullmatch(r"[0-9a-f]{32}", recovery):
            raise ValueError("Invalid recovery revision")
        path = STATE / "checkpoints" / f"{recovery}.blend"
        metadata = read_json(path.with_suffix(".json"))
        if metadata and file_hash(path) != metadata["sha256"]:
            raise ValueError("Selected recovery checkpoint checksum mismatch")
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
        checkpoint()
    elif latest:
        checkpoint_path = STATE / "checkpoints" / Path(latest["path"]).name
        if file_hash(checkpoint_path) != latest["sha256"]:
            raise ValueError("Saved checkpoint checksum mismatch; recovery refused")
        bpy.ops.wm.open_mainfile(filepath=str(checkpoint_path), load_ui=False, use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=False)
        checkpoint()
    bpy.context.preferences.view.show_splash = False
    write_json(
        STATE / "worker.json",
        dict(
            status="ready",
            pid=os.getpid(),
            blender=bpy.app.version_string,
            protocol=PROTOCOL,
            session_id=os.environ.get("RBM_SESSION_ID"),
        ),
    )
    if bpy.app.background:
        while True:
            time.sleep(process_next())
    else:
        bpy.app.timers.register(process_next, first_interval=0.5, persistent=True)


if __name__ == "__main__":
    main()
