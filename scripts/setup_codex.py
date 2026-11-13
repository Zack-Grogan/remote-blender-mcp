"""Install this checkout's MCP entry in its project Codex configuration."""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install(root=ROOT, uv=None):
    uv = uv or shutil.which("uv")
    if not uv:
        raise RuntimeError("Install uv before configuring Codex")
    path = root / ".codex" / "config.toml"
    section = "[mcp_servers.remote_blender]"
    stanza = (
        f"{section}\ncommand = {json.dumps(uv)}\n"
        f"args = {json.dumps(['run', '--locked', '--directory', str(root), 'remote-blender-mcp'])}\n"
        'env_vars = ["MODAL_PROFILE", "MODAL_CONFIG_PATH", "RBM_STATE_DIR", "RBM_MODAL_APP"]\nstartup_timeout_sec = 20\ntool_timeout_sec = 600\ndefault_tools_approval_mode = "approve"\n'
    )
    content = path.read_text() if path.exists() else ""
    if section in content:
        start = content.index(section)
        end = content.find("\n[", start + len(section))
        end = len(content) if end == -1 else end
        content = content[:start] + stanza + content[end:]
    else:
        content = content.rstrip() + ("\n\n" if content.strip() else "") + stanza
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


if __name__ == "__main__":
    print(install())
