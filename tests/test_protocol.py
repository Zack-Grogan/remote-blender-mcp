import importlib.util
import json
from pathlib import Path

import pytest

from remote_blender_mcp.controller import Controller


def load_common(monkeypatch, tmp_path):
    monkeypatch.setenv("RBM_WORKSPACE", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "common_test", Path(__file__).parents[1] / "src/remote_blender_mcp/remote/common.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_file_queue_and_paths(monkeypatch, tmp_path):
    common = load_common(monkeypatch, tmp_path)
    common.STATE.mkdir()
    job = common.submit("inspect", {})
    assert common.read_json(common.job_path(job["id"]))["status"] == "queued"
    assert common.workspace_path("assets/texture.png") == tmp_path / "assets/texture.png"
    for bad in ["../outside", "/etc/passwd", ".rbm/latest.json", "."]:
        with pytest.raises(ValueError):
            common.workspace_path(bad)
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        common.workspace_path("escape/private")
    with pytest.raises(ValueError):
        common.job_path("../../file")


def test_checkpoint_publication_is_atomic(monkeypatch, tmp_path):
    common = load_common(monkeypatch, tmp_path)
    path = tmp_path / "record.json"
    common.write_json(path, {"revision": "a"})
    with pytest.raises(ValueError):
        common.write_json(path, {"invalid": float("nan")})
    assert json.loads(path.read_text()) == {"revision": "a"}


def test_controller_validates_before_cloud(tmp_path):
    c = Controller(tmp_path)
    for project in ["../bad", "CAPS", "", "a" * 49]:
        with pytest.raises(ValueError):
            c.create(project)
    with pytest.raises(ValueError):
        c.create("valid", lifetime_seconds=100)
    with pytest.raises(ValueError):
        c.create("valid", gpu="unknown")


def test_download_verifies_before_publishing(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    c = Controller(tmp_path / "registry")
    content = b"verified artifact"

    def copy(remote, local):
        Path(local).write_bytes(content)

    monkeypatch.setattr(
        c, "sandbox", lambda _: SimpleNamespace(filesystem=SimpleNamespace(copy_to_local=copy))
    )
    expected = {
        "path": "/workspace/file",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    monkeypatch.setattr(c, "rpc", lambda *args, **kwargs: expected)
    target = tmp_path / "download"
    assert c.download("session", "file", str(target))["sha256"] == expected["sha256"]
    assert target.read_bytes() == content
    with pytest.raises(ValueError):
        c.download("session", "file", str(target))
    assert target.read_bytes() == content
    expected["sha256"] = "incorrect"
    bad = tmp_path / "bad"
    with pytest.raises(RuntimeError, match="checksum"):
        c.download("session", "file", str(bad))
    assert not bad.exists()


def test_shutdown_barrier_and_cancellation(monkeypatch, tmp_path):
    common = load_common(monkeypatch, tmp_path)
    first = common.submit("python", {"code": "first"})
    queued = common.submit("python", {"code": "must never execute"})
    common.cancel_job(queued["id"])
    stop = common.begin_stop()
    assert common.begin_stop()["id"] == stop["id"]
    with pytest.raises(ValueError, match="draining"):
        common.submit("python", {"code": "too late"})
    active = common.claim_next()
    assert active["id"] == first["id"]
    common.finish_job({**active, "status": "succeeded"})
    active = common.claim_next()
    assert active["id"] == stop["id"]
    common.finish_job({**active, "status": "failed"})
    assert common.submit("python", {"code": "fix save problem"})["status"] == "queued"
    assert common.read_json(common.job_path(queued["id"]))["status"] == "cancelled"


def test_stale_completion_cannot_overwrite_terminal_state(monkeypatch, tmp_path):
    common = load_common(monkeypatch, tmp_path)
    common.submit("python", {})
    active = common.claim_next()
    with common.control_lock():
        common.write_json(common.job_path(active["id"]), {**active, "status": "timed_out"})
    assert not common.finish_job({**active, "status": "succeeded"})
    assert common.read_json(common.job_path(active["id"]))["status"] == "timed_out"


def test_parallel_submitters_do_not_lose_jobs(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys

    common = load_common(monkeypatch, tmp_path)
    remote = Path(__file__).parents[1] / "src/remote_blender_mcp/remote"
    env = {**os.environ, "PYTHONPATH": str(remote), "RBM_WORKSPACE": str(tmp_path)}
    script = "from common import submit; [submit('inspect', {'n': n}) for n in range(15)]"
    processes = [subprocess.Popen([sys.executable, "-c", script], env=env) for _ in range(3)]
    for process in processes:
        assert process.wait(timeout=10) == 0
    claimed = []
    while (job := common.claim_next()) is not None:
        claimed.append(job["id"])
        common.finish_job({**job, "status": "succeeded"})
    assert len(set(claimed)) == 45
    assert all(common.read_json(p)["status"] == "succeeded" for p in common.JOBS.glob("*.json"))


def test_listing_excludes_symlinks_and_internal_state(monkeypatch, tmp_path):
    import sys

    common = load_common(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "common", common)
    spec = importlib.util.spec_from_file_location(
        "rpc_test", Path(__file__).parents[1] / "src/remote_blender_mcp/remote/rpc.py"
    )
    rpc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rpc)
    common.STATE.mkdir()
    (common.STATE / "private.json").write_text("private")
    (tmp_path / "internal-link").symlink_to(common.STATE, target_is_directory=True)
    (tmp_path / "outside-link").symlink_to(tmp_path.parent, target_is_directory=True)
    (tmp_path / "visible.txt").write_text("visible")
    (tmp_path / "file-link").symlink_to(common.STATE / "private.json")
    assert rpc.main("files", {}) == [{"path": "visible.txt", "size": 7}]
    with pytest.raises(ValueError):
        common.workspace_path(str(tmp_path / "visible.txt"))


def test_slow_project_does_not_lock_unrelated_projects(tmp_path):
    import threading

    c = Controller(tmp_path)
    acquired = threading.Event()

    def other_project():
        with c._lock("cpu-project"):
            acquired.set()

    with c._lock("slow-gpu-project"):
        thread = threading.Thread(target=other_project)
        thread.start()
        assert acquired.wait(timeout=2), "Unrelated provisioning blocked by a global lock"
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize("owned", [True, False])
def test_provision_timeout_cancels_only_its_own_allocation(monkeypatch, owned):
    import asyncio
    from types import SimpleNamespace
    import remote_blender_mcp.controller as module

    terminated = []

    async def create(*args, **kwargs):
        await asyncio.sleep(10)

    async def tags():
        return {"rbm_session": "ours" if owned else "unrelated"}

    async def terminate(**kwargs):
        terminated.append(True)

    async def lookup(*args):
        return SimpleNamespace(get_tags=SimpleNamespace(aio=tags), terminate=SimpleNamespace(aio=terminate))

    monkeypatch.setattr(module, "PROVISION_SECONDS", 0.01)
    monkeypatch.setattr(
        module.modal,
        "Sandbox",
        SimpleNamespace(create=SimpleNamespace(aio=create), from_name=SimpleNamespace(aio=lookup)),
    )
    with pytest.raises(module.ControlTimeout, match="provisioning exceeded") as error:
        module.provision(command=["python"], name="test", tags={"rbm_session": "ours"})
    assert bool(terminated) == owned
    assert ("allocation cancelled" in str(error.value)) == owned


def test_cancelled_stop_barrier_reopens_submission(monkeypatch, tmp_path):
    common = load_common(monkeypatch, tmp_path)
    stopping = common.begin_stop()
    common.cancel_job(stopping["id"])
    assert common.submit("inspect", {})["status"] == "queued"


def test_dynamic_enum_inspection_resolves_mixin_registration(monkeypatch):
    import sys
    from types import SimpleNamespace

    fake_bpy = SimpleNamespace(context=object())
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    path = Path(__file__).parents[1] / "src/remote_blender_mcp/remote/inspection.py"
    spec = importlib.util.spec_from_file_location("inspection_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prop = SimpleNamespace(
        identifier="export_format", type="ENUM", description="", is_readonly=False, default="", enum_items=[]
    )
    rna = SimpleNamespace(identifier="TEST_OT_export", description="", properties=[prop])

    class Mixin:
        __annotations__ = {
            "export_format": SimpleNamespace(
                keywords={
                    "items": lambda self, context: [("GLB", "Binary", ""), ("GLTF_SEPARATE", "Separate", "")]
                }
            )
        }

    class Registered(Mixin):
        pass

    resolved = module.properties(rna, 0, 50, Registered, object())["properties"][0]
    assert resolved["items_complete"] and resolved["context_dependent"]
    assert resolved["default"] is None
    assert [v["identifier"] for v in resolved["items"]] == ["GLB", "GLTF_SEPARATE"]
    unresolved = module.properties(rna, 0, 50)["properties"][0]
    assert not unresolved["items_complete"]
    assert "does not mean no choices" in unresolved["items_note"]
