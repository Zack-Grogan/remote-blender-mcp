import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_discovery_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("RBM_STATE_DIR", str(tmp_path))

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "remote_blender_mcp"], env={"RBM_STATE_DIR": str(tmp_path)}
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"execute_python", "render", "view_image", "restore_checkpoint"} <= names
                result = await session.call_tool("list_sessions", {})
                assert not result.isError
                result = await session.call_tool("create_session", {"project": "../invalid"})
                assert result.isError
                result = await session.call_tool("render", {"session_id": "bad", "width": 1})
                assert result.isError

    asyncio.run(run())


def test_viewport_tools_have_client_compatible_schemas(tmp_path, monkeypatch):
    monkeypatch.setenv("RBM_STATE_DIR", str(tmp_path))

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "remote_blender_mcp"], env={"RBM_STATE_DIR": str(tmp_path)}
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert "gui" in tools["create_session"].inputSchema["properties"]["mode"]["enum"]
                assert "rotation" in tools["control_viewport"].inputSchema["properties"]
                assert "viewport_screenshot" in tools
                assert "new_workspace" in tools["create_session"].inputSchema["properties"]
                assert "workspace_id" in tools["create_session"].inputSchema["properties"]
                assert len(tools) == 26
                assert not any("batch" in name or "task" in name for name in tools)
                invalid = await session.call_tool("control_viewport", {"session_id": "bad", "distance": -1})
                assert invalid.isError

    asyncio.run(run())
