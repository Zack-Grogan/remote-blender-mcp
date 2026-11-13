"""Local Modal control plane. No Blender installation is needed locally."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import modal

REMOTE = Path(__file__).parent / "remote"
STATE_DIR = Path(os.environ.get("RBM_STATE_DIR", "~/.local/state/remote-blender-mcp")).expanduser()
PROTOCOL = 2
PROVISION_SECONDS = 180
CONTROL_SECONDS = 35
APP_NAME = os.environ.get("RBM_MODAL_APP", "remote-blender-mcp")


class ControlTimeout(RuntimeError):
    pass


def provision(**kwargs):
    command = kwargs.pop("command")

    async def create():
        try:
            return await asyncio.wait_for(
                modal.Sandbox.create.aio(*command, **kwargs), timeout=PROVISION_SECONDS
            )
        except TimeoutError:
            # Only terminate this request's tagged sandbox, never an unrelated named service.
            async def cleanup():
                try:
                    sandbox = await modal.Sandbox.from_name.aio(APP_NAME, kwargs["name"])
                    tags = await sandbox.get_tags.aio()
                    if tags.get("rbm_session") == kwargs["tags"]["rbm_session"]:
                        await sandbox.terminate.aio(wait=False)
                        return True
                except modal.exception.NotFoundError:
                    return False
                return False

            try:
                confirmed = await asyncio.wait_for(cleanup(), timeout=15)
            except Exception:
                confirmed = False
            outcome = (
                "allocation cancelled"
                if confirmed
                else f"cancellation not confirmed; inspect sandbox {kwargs['name']}"
            )
            raise ControlTimeout(f"Sandbox provisioning exceeded {PROVISION_SECONDS}s; {outcome}")

    return asyncio.run(create())


class Controller:
    def __init__(self, state_dir: Path = STATE_DIR):
        self.state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, session_id):
        uuid.UUID(session_id)
        return self.state_dir / f"{session_id}.json"

    def record(self, session_id):
        return json.loads(self._path(session_id).read_text())

    def _write(self, record):
        path = self._path(record["session_id"])
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(record, indent=2))
        tmp.replace(path)

    @contextmanager
    def _lock(self, project):
        key = hashlib.sha256(project.encode()).hexdigest()[:16]
        with (self.state_dir / f"controller-{key}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def sandbox(self, session_id):
        record = self.record(session_id)
        try:
            sb = modal.Sandbox.from_id(record["sandbox_id"])
        except modal.exception.NotFoundError as exc:
            raise RuntimeError("Session expired; create another session for this project") from exc
        if sb.poll() is not None:
            hint = (
                f"workspace_id={record['workspace_id']}" if record.get("workspace_id") else "the same project"
            )
            raise RuntimeError(
                f"Session ended. Create another session using {hint} to restore its checkpoint."
            )
        return sb

    def create(
        self,
        project,
        gpu=None,
        lifetime_seconds=3600,
        idle_seconds=900,
        mode="headless",
        restore_revision=None,
        *,
        new_workspace=False,
        workspace_id=None,
    ):
        if new_workspace and workspace_id:
            raise ValueError("Choose a new workspace or an existing workspace_id, not both")
        if new_workspace and restore_revision:
            raise ValueError("To restore an isolated checkpoint, supply its workspace_id")
        if workspace_id:
            workspace_id = str(uuid.UUID(workspace_id))
        elif new_workspace:
            workspace_id = str(uuid.uuid4())
        workspace_name = f"i_{uuid.UUID(workspace_id).hex}" if workspace_id else project
        if restore_revision is not None and not re.fullmatch(r"[0-9a-f]{32}", restore_revision):
            raise ValueError("Recovery revision must be a 32-character checkpoint ID")
        if mode not in ("headless", "gui"):
            raise ValueError("Mode must be headless or gui")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", project):
            raise ValueError("Project must be 1–48 lowercase letters, digits or hyphens")
        if not 120 <= lifetime_seconds <= 86400 or not 60 <= idle_seconds <= lifetime_seconds:
            raise ValueError("Lifetime must be 120–86400 seconds; idle must be 60–lifetime")
        if gpu not in (None, "T4", "L4", "A10G", "L40S"):
            raise ValueError("Supported prototype GPU profiles: T4, L4, A10G, L40S")
        runtime_hash = hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted(REMOTE.glob("*.py")))
        ).hexdigest()
        with self._lock(workspace_name):
            for record in self.list():
                if record.get("workspace_name", record["project"]) == workspace_name and not record.get(
                    "stopped"
                ):
                    try:
                        sb = modal.Sandbox.from_id(record["sandbox_id"])
                        live = sb.poll() is None
                    except modal.exception.NotFoundError:
                        live = False
                    if live:
                        if new_workspace:
                            raise RuntimeError(
                                "Fresh workspace identity collision; request another workspace"
                            )
                        if restore_revision:
                            raise ValueError("Stop the active session before choosing a recovery revision")
                        if record.get("runtime_hash") != runtime_hash:
                            raise ValueError(
                                "Active session uses an older runtime; stop it before creating a new one"
                            )
                        if record["gpu"] != gpu:
                            raise ValueError(
                                "Workspace already has an active session with a different GPU profile; stop it first"
                            )
                        if record.get("mode", "headless") != mode:
                            raise ValueError("Workspace has an active session in another mode; stop it first")
                        return {**record, "reused": True}
            app = modal.App.lookup(APP_NAME, create_if_missing=True)
            image = (
                modal.Image.debian_slim(python_version="3.11")
                .apt_install("libgl1", "libxi6", "libxrender1", "libxkbcommon0", "libsm6")
                .pip_install("bpy==4.5.0")
            )
            if mode == "gui":
                image = image.apt_install(
                    "curl",
                    "xz-utils",
                    "xvfb",
                    "libgl1-mesa-dri",
                    "libegl1",
                    "libxfixes3",
                    "libxxf86vm1",
                    "libxcursor1",
                    "libxrandr2",
                    "libxinerama1",
                    "libdbus-1-3",
                ).run_commands(
                    "curl -fsSL https://download.blender.org/release/Blender4.5/blender-4.5.0-linux-x64.tar.xz -o /tmp/blender.tar.xz",
                    "echo '1188b95cc12321c770b631939f7c25a096910b6f884a990bf9c0f62d52b38aec  /tmp/blender.tar.xz' | sha256sum -c -",
                    "mkdir /opt/blender && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 && rm /tmp/blender.tar.xz",
                    '/opt/blender/blender -b --factory-startup --python-expr "import bpy; bpy.context.preferences.view.show_splash=False; bpy.ops.wm.save_userpref()"',
                )
            image = image.add_local_dir(REMOTE, "/opt/rbm", copy=True)
            volume_name = f"rbm-{workspace_name}"
            volume = modal.Volume.from_name(volume_name, create_if_missing=True, version=2)
            session_id = str(uuid.uuid4())
            sb = provision(
                command=["python", "/opt/rbm/supervisor.py"],
                app=app,
                image=image,
                name=f"rbm-{workspace_name}",
                tags={"rbm_session": session_id},
                volumes={"/workspace": volume},
                gpu=gpu,
                cpu=(1, 4),
                memory=(2048, 8192),
                timeout=lifetime_seconds,
                workdir="/workspace",
                env={
                    "RBM_IDLE_SECONDS": str(idle_seconds),
                    "RBM_MODE": mode,
                    "RBM_SESSION_ID": session_id,
                    "RBM_RESTORE_REVISION": restore_revision or "",
                },
                block_network=True,
            )
            record = dict(
                session_id=session_id,
                runtime_hash=runtime_hash,
                protocol=PROTOCOL,
                project=project,
                workspace_name=workspace_name,
                workspace_id=workspace_id,
                sandbox_id=sb.object_id,
                volume=volume_name,
                gpu=gpu,
                mode=mode,
                created_at=time.time(),
                lifetime_seconds=lifetime_seconds,
                idle_seconds=idle_seconds,
            )
            self._write(record)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if sb.poll() is not None:
                    raise RuntimeError(
                        f"Blender startup failed; inspect session_status/get_logs for {session_id}"
                    )
                try:
                    status = self._call(sb, "status")
                except ControlTimeout:
                    continue
                worker = status.get("worker") or {}
                if worker.get("status") == "ready" and worker.get("session_id") == session_id:
                    return {**record, "worker": worker}
                time.sleep(0.5)
            raise RuntimeError(
                f"Blender not ready within 120s; session {session_id} remains available for diagnostics/stop"
            )

    def list(self):
        return [json.loads(p.read_text()) for p in self.state_dir.glob("*.json")]

    def _call(self, sb, action, legacy=False, **payload):
        async def perform():
            if legacy:
                process = await sb.exec.aio(
                    "python", "/opt/rbm/rpc.py", action, json.dumps(payload), timeout=30
                )
            else:
                process = await sb.exec.aio("python", "/opt/rbm/rpc.py", action, timeout=30)
                process.stdin.write(json.dumps(payload, allow_nan=False).encode())
                process.stdin.write_eof()
                await process.stdin.drain.aio()
            output = await process.stdout.read.aio()
            await process.wait.aio()
            if process.returncode:
                raise RuntimeError(output or await process.stderr.read.aio())
            return json.loads(output)

        async def bounded():
            try:
                return await asyncio.wait_for(perform(), timeout=CONTROL_SECONDS)
            except TimeoutError as exc:
                raise ControlTimeout(
                    "Remote request exceeded 35s; its outcome may be unknown. Inspect session/job status before retrying mutations"
                ) from exc

        return asyncio.run(bounded())

    def _volume_json(self, record, path):
        volume = modal.Volume.from_name(record["volume"])
        try:
            return json.loads(b"".join(volume.read_file(path)))
        except (FileNotFoundError, modal.exception.NotFoundError):
            return None

    def rpc(self, session_id, action, **payload):
        record = self.record(session_id)
        try:
            sb = self.sandbox(session_id)
        except RuntimeError:
            if action == "status":
                if payload.get("job_id"):
                    job = self._volume_json(record, f".rbm/jobs/{uuid.UUID(payload['job_id'])}.json")
                    if job is None:
                        raise ValueError("Unknown job ID")
                    if job["status"] in ("queued", "running"):
                        return {
                            **job,
                            "status": "failed",
                            "error": "Sandbox ended before job completion",
                            "partial_change_possible": True,
                        }
                    return job
                return {
                    **record,
                    "status": "ended",
                    "worker": self._volume_json(record, ".rbm/worker.json"),
                    "checkpoint": self._volume_json(record, ".rbm/latest.json"),
                }
            if action == "checkpoints":
                volume = modal.Volume.from_name(record["volume"])
                return sorted(
                    [
                        dict(revision=Path(e.path).stem, size=e.size, saved_at=e.mtime)
                        for e in volume.listdir(".rbm/checkpoints")
                        if e.path.endswith(".blend")
                    ],
                    key=lambda e: e["saved_at"],
                    reverse=True,
                )
            if action == "logs":
                return {"session_ended": True, "worker": self._volume_json(record, ".rbm/worker.json")}
            raise
        return self._call(sb, action, legacy=record.get("protocol", 1) < PROTOCOL, **payload)

    def submit(self, session_id, kind, params, timeout=120):
        if not 1 <= timeout <= 3600:
            raise ValueError("Job timeout must be 1–3600 seconds")
        return self.rpc(session_id, "submit", kind=kind, params=params, timeout=timeout)

    def wait(self, session_id, job_id, seconds=20):
        deadline = time.monotonic() + min(max(seconds, 0), 30)
        while True:
            job = self.rpc(session_id, "status", job_id=job_id)
            if job["status"] not in ("queued", "running") or time.monotonic() >= deadline:
                return job
            time.sleep(0.25)

    def stop(self, session_id, discard_unsaved=False):
        record = self.record(session_id)
        try:
            sb = modal.Sandbox.from_id(record["sandbox_id"])
        except modal.exception.NotFoundError:
            return {"stopped": True, "already_ended": True}
        if sb.poll() is not None:
            return {"stopped": True, "already_ended": True}
        if not discard_unsaved:
            job = (
                self.rpc(session_id, "begin_stop")
                if record.get("protocol", 1) >= PROTOCOL
                else self.submit(session_id, "save", {})
            )
            result = self.wait(session_id, job["id"], 30)
            if result["status"] in ("queued", "running"):
                return {"stopped": False, "draining": True, "job_id": job["id"]}
            if result["status"] != "succeeded":
                raise RuntimeError(
                    f"Save failed; compute remains active. Inspect job {job['id']} or stop with discard_unsaved=True"
                )
        sb.terminate(wait=True)
        record["stopped"] = True
        self._write(record)
        return {
            "stopped": True,
            "discarded_unsaved": discard_unsaved,
            "checkpoint": None if discard_unsaved else result["result"],
        }

    def upload(self, session_id, local_path, remote_path):
        source = Path(local_path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError("Upload one file at a time")
        destination = self.rpc(session_id, "prepare_upload", path=remote_path)["path"]
        self.sandbox(session_id).filesystem.copy_from_local(str(source), destination)
        remote = self.rpc(session_id, "artifact", path=remote_path)
        with source.open("rb") as data:
            digest = hashlib.file_digest(data, "sha256").hexdigest()
        if remote["sha256"] != digest:
            raise RuntimeError("Upload checksum mismatch")
        return dict(path=remote_path, size=source.stat().st_size, sha256=remote["sha256"])

    def download(self, session_id, remote_path, local_path):
        target = Path(local_path).expanduser().absolute()
        if target.exists():
            raise ValueError("Local destination exists; choose a new filename")
        artifact = self.rpc(session_id, "artifact", path=remote_path)
        if artifact["size"] > 5_000_000_000:
            raise ValueError("Prototype transfer limit is 5 GB")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            staging = Path(temp) / "download"
            self.sandbox(session_id).filesystem.copy_to_local(artifact["path"], str(staging))
            with staging.open("rb") as data:
                digest = hashlib.file_digest(data, "sha256").hexdigest()
            if digest != artifact["sha256"]:
                raise RuntimeError("Download checksum mismatch")
            # Atomic publication on the same filesystem; an existing target fails.
            os.link(staging, target)

        return dict(path=str(target), size=target.stat().st_size, sha256=digest)

    def image(self, session_id, path):
        artifact = self.rpc(session_id, "artifact", path=path)
        if not path.lower().endswith(".png") or artifact["size"] > 10_000_000:
            raise ValueError("Inline images must be PNG files under 10 MB")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "image.png"
            self.sandbox(session_id).filesystem.copy_to_local(artifact["path"], str(target))
            return target.read_bytes()
