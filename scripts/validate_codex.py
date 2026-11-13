"""Exercise viewport tools through the actual authenticated Codex CLI.

Uses project MCP configuration and an isolated temporary HOME and trust registry. Authentication is
referenced by a temporary symlink; no credentials are copied into the project.
The caller owns session lifecycle and must stop the session after validation.
"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_events(path):
    calls = []
    image_blocks = []
    for line in path.read_text().splitlines():
        event = json.loads(line)
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call":
            calls.append(
                {"tool": item.get("tool"), "status": item.get("status"), "server": item.get("server")}
            )
            if item.get("status") != "completed":
                raise RuntimeError(f"MCP call failed: {item.get('tool')}")
            if item.get("tool") == "viewport_screenshot":
                image_blocks.append(
                    sum(c.get("type") == "image" for c in (item.get("result") or {}).get("content", []))
                )
    required = {"inspect_scene", "execute_python", "control_viewport", "viewport_screenshot"}
    if not required <= {call["tool"] for call in calls}:
        raise RuntimeError(f"Missing required MCP calls: {calls}")
    if len(image_blocks) < 2 or not all(image_blocks):
        raise RuntimeError("Codex did not receive both inline viewport images")
    return calls, image_blocks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    args = parser.parse_args()
    out = ROOT / ".validation"
    out.mkdir(exist_ok=True)
    user_home = Path.home()
    auth = Path(os.environ.get("CODEX_HOME", str(user_home / ".codex"))) / "auth.json"
    prompt = f"""Validate the remote Blender MCP using only its tools. No shell, files, web, skills or other tools.
The existing GUI session ID is {args.session_id}. Do not create or stop any session.
1. Inspect the scene.
2. Execute Python to create a cube named CodexViewportProbe if absent, at (0,0,0), and assign a material with diffuse_color (0.05,0.8,0.1,1). Set result to its name. Keep checkpoint enabled. Poll job_status until terminal.
3. control_viewport: select ["CodexViewportProbe"], frame_selected true, view FRONT, shading SOLID.
4. viewport_screenshot and inspect the actual returned image. Record the artifact path. If still queued, poll and use view_image.
5. control_viewport: select ["CodexViewportProbe"], view ISO, shading WIREFRAME, distance 7.
6. viewport_screenshot and inspect that returned image. Record the artifact path.
7. Briefly describe the visible difference between the two images. Report failures honestly.
Return JSON with front_image, wire_image, visible_difference, success. success is true only if both actual images were inspected and the calls succeeded.
Do not edit repository files, run subprocesses, use delegation or load any skills."""
    with tempfile.TemporaryDirectory(prefix="rbm-codex-") as temp:
        home = Path(temp)
        codex_home = home / ".codex"
        codex_home.mkdir()
        if auth.exists():
            (codex_home / "auth.json").symlink_to(auth)
        (codex_home / "config.toml").write_text(
            f'[projects.{json.dumps(str(ROOT))}]\ntrust_level="trusted"\n'
        )
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CODEX_")},
            "HOME": temp,
            "CODEX_HOME": str(codex_home),
            "MODAL_CONFIG_PATH": os.environ.get("MODAL_CONFIG_PATH", str(user_home / ".modal.toml")),
            "RBM_STATE_DIR": os.environ.get(
                "RBM_STATE_DIR", str(user_home / ".local/state/remote-blender-mcp")
            ),
        }
        command = [
            "codex",
            "exec",
            "--ignore-rules",
            "--ephemeral",
            "--json",
            "--sandbox",
            "read-only",
            "-c",
            'approval_policy="never"',
            "-c",
            f'projects.{json.dumps(str(ROOT))}.trust_level="trusted"',
            "-C",
            str(ROOT),
            "-o",
            str(out / "codex-final.json"),
            prompt,
        ]
        with (out / "codex-events.jsonl").open("w") as stdout, (out / "codex-stderr.log").open("w") as stderr:
            result = subprocess.run(command, env=env, stdout=stdout, stderr=stderr, timeout=480)
        if result.returncode:
            raise RuntimeError(f"Codex exited {result.returncode}; inspect .validation/codex-stderr.log")
    calls, image_blocks = validate_events(out / "codex-events.jsonl")
    final = (out / "codex-final.json").read_text().strip()
    if final.startswith("```"):
        final = "\n".join(final.splitlines()[1:-1])
    final = json.loads(final)
    if final.get("success") is not True:
        raise RuntimeError(f"Codex reported a failure: {final}")
    report = {
        "client": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "calls": calls,
        "inline_image_blocks": image_blocks,
        "result": final,
    }
    (out / "codex-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
