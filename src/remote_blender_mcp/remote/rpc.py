"""One-shot control command; Blender code never runs in this process."""

import hashlib
import json
import sys
import time
from pathlib import Path
from common import (
    JOBS,
    ROOT,
    STATE,
    PROTOCOL,
    begin_stop,
    cancel_job,
    job_path,
    read_json,
    submit,
    workspace_path,
)


def main(action, payload):
    STATE.mkdir(parents=True, exist_ok=True)
    if action != "status" or not payload.get("job_id"):
        (STATE / "touch").touch()
    if action == "begin_stop":
        return begin_stop()
    if action == "submit":
        return submit(**payload)
    if action == "status":
        if payload.get("job_id"):
            job = read_json(job_path(payload["job_id"]))
            if job is None:
                raise ValueError("Unknown job ID")
            return job
        (STATE / "touch").touch()
        return dict(
            protocol=PROTOCOL,
            control=read_json(STATE / "control.json"),
            worker=read_json(STATE / "worker.json"),
            checkpoint=read_json(STATE / "latest.json"),
            jobs=[
                {
                    k: j[k]
                    for k in ("id", "kind", "status", "created_at", "started_at", "finished_at")
                    if k in j
                }
                for j in sorted([read_json(p) for p in JOBS.glob("*.json")], key=lambda j: j["created_at"])[
                    -50:
                ]
            ],
        )
    if action == "checkpoints":
        return [
            dict(revision=p.stem, size=p.stat().st_size, saved_at=p.stat().st_mtime)
            for p in sorted(
                (STATE / "checkpoints").glob("*.blend"), key=lambda p: p.stat().st_mtime, reverse=True
            )
        ]
    if action == "cancel":
        return cancel_job(payload["job_id"])
    if action == "logs":
        path = STATE / "worker.log"
        if not path.exists():
            return {"text": "", "bytes": 0}
        with path.open("rb") as data:
            data.seek(max(0, path.stat().st_size - min(payload.get("max_bytes", 16000), 64000)))
            content = data.read(64000)
        return {"text": content.decode("utf-8", errors="replace"), "bytes": len(content)}
    if action == "files":
        import os

        prefix = payload.get("path", "")
        base = ROOT if not prefix else workspace_path(prefix)
        if not base.is_dir():
            raise ValueError("File listing requires an existing directory")
        files = []
        for directory, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if not (Path(directory) / d).is_symlink()
                and not (Path(directory) / d).resolve().is_relative_to(STATE)
            )
            for name in sorted(names):
                p = Path(directory) / name
                if not p.is_symlink() and p.is_file() and not p.resolve().is_relative_to(STATE):
                    files.append(dict(path=str(p.relative_to(ROOT)), size=p.stat().st_size))
        offset, limit = payload.get("offset", 0), payload.get("limit", 1000)
        return files[offset : offset + limit]
    if action == "prepare_upload":
        p = workspace_path(payload["path"])
        if p.exists():
            raise ValueError("Remote path exists; choose a new path")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=False)
        return {"path": str(p)}
    if action == "artifact":
        p = workspace_path(payload["path"])
        with p.open("rb") as data:
            digest = hashlib.file_digest(data, "sha256").hexdigest()
        return {"path": str(p), "size": p.stat().st_size, "sha256": digest}
    raise ValueError(f"Unknown action: {action}")


if __name__ == "__main__":
    try:
        print(json.dumps(main(sys.argv[1], json.loads(sys.stdin.read()))))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "time": time.time()}))
        sys.exit(1)
