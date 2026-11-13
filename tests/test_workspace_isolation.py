import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import remote_blender_mcp.controller as module
from remote_blender_mcp.controller import Controller


@pytest.fixture
def simulated_modal(monkeypatch):
    sandboxes = {}
    calls = []
    gate = threading.Event()
    release = threading.Event()
    blocking = {"enabled": False}

    class Image:
        def apt_install(self, *args):
            return self

        def pip_install(self, *args):
            return self

        def add_local_dir(self, *args, **kwargs):
            return self

    def provision(**kwargs):
        sid = kwargs["env"]["RBM_SESSION_ID"]
        sb = SimpleNamespace(object_id="sb-" + sid, session_id=sid, exit_code=None)
        sb.poll = lambda: sb.exit_code
        sandboxes[sb.object_id] = sb
        first = not calls
        calls.append(kwargs)
        if first and blocking["enabled"]:
            gate.set()
            assert release.wait(4)
        return sb

    monkeypatch.setattr(module.modal.App, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(module.modal.Image, "debian_slim", lambda **kwargs: Image())
    monkeypatch.setattr(module.modal.Volume, "from_name", lambda name, **kwargs: name)
    monkeypatch.setattr(module.modal.Sandbox, "from_id", lambda sid: sandboxes[sid])
    monkeypatch.setattr(module, "provision", provision)
    monkeypatch.setattr(
        Controller,
        "_call",
        lambda self, sb, *args, **kwargs: {"worker": {"status": "ready", "session_id": sb.session_id}},
    )
    return SimpleNamespace(calls=calls, sandboxes=sandboxes, gate=gate, release=release, blocking=blocking)


def test_fresh_workspaces_share_label_but_not_resources(tmp_path, simulated_modal):
    c = Controller(tmp_path)
    first = c.create("shared-label", new_workspace=True)
    second = c.create("shared-label", new_workspace=True)
    assert first["project"] == second["project"] == "shared-label"
    for field in ["session_id", "workspace_id", "workspace_name", "volume", "sandbox_id"]:
        assert first[field] != second[field]
    assert c.create("shared-label", workspace_id=first["workspace_id"])["session_id"] == first["session_id"]
    default = c.create("shared-label")
    assert default["volume"] not in {first["volume"], second["volume"]}
    assert c.create("shared-label")["session_id"] == default["session_id"]
    assert len(simulated_modal.calls) == 3
    with pytest.raises(ValueError):
        c.create(first["workspace_name"])  # Isolated physical namespace cannot be a public project slug.


def test_workspace_recovery_reuses_volume_not_ended_compute(tmp_path, simulated_modal):
    c = Controller(tmp_path)
    first = c.create("same-project", new_workspace=True)
    simulated_modal.sandboxes[first["sandbox_id"]].exit_code = 0
    reopened = c.create("same-project", workspace_id=first["workspace_id"], restore_revision="a" * 32)
    assert reopened["workspace_id"] == first["workspace_id"]
    assert reopened["volume"] == first["volume"]
    assert reopened["sandbox_id"] != first["sandbox_id"]
    assert simulated_modal.calls[-1]["env"]["RBM_RESTORE_REVISION"] == "a" * 32


def test_unrelated_workspaces_do_not_serialize_provisioning(tmp_path, simulated_modal):
    c = Controller(tmp_path)
    simulated_modal.blocking["enabled"] = True
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(c.create, "same-project", new_workspace=True)
        assert simulated_modal.gate.wait(2)
        second = pool.submit(c.create, "same-project", new_workspace=True)
        try:
            ready = second.result(timeout=2)
            assert ready["workspace_id"]
        finally:
            simulated_modal.release.set()
        assert first.result(timeout=2)["workspace_id"] != ready["workspace_id"]


def test_invalid_workspace_combinations_do_not_allocate(tmp_path, simulated_modal):
    c = Controller(tmp_path)
    for kwargs in [
        {"new_workspace": True, "workspace_id": "a" * 32},
        {"new_workspace": True, "restore_revision": "b" * 32},
        {"workspace_id": "../../bad"},
    ]:
        with pytest.raises(ValueError):
            c.create("project", **kwargs)
    assert not simulated_modal.calls
