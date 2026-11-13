import importlib.util
import tomllib
from pathlib import Path


def test_codex_setup_preserves_other_settings_and_is_repeatable(tmp_path):
    path = Path(__file__).parents[1] / "scripts/setup_codex.py"
    spec = importlib.util.spec_from_file_location("setup_codex", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('model = "existing-model"\n\n[mcp_servers.other]\ncommand = "other"\n')
    module.install(tmp_path, "/test/uv")
    first = config.read_text()
    module.install(tmp_path, "/test/uv")
    assert config.read_text() == first
    parsed = tomllib.loads(first)
    assert parsed["model"] == "existing-model"
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert parsed["mcp_servers"]["remote_blender"]["args"][3] == str(tmp_path)
