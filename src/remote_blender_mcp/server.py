"""MCP tools for persistent Blender sessions on Modal."""

import argparse
import asyncio
from functools import wraps

from mcp.server.fastmcp import FastMCP, Image
from pydantic import Field
from typing import Annotated, Literal

from .controller import Controller

mcp = FastMCP(
    "remote-blender",
    log_level="WARNING",
    instructions="Blender runs remotely on Modal. Create a session with mode=gui for live viewport control, or headless for batch work; inspect before editing, use execute_python for full bpy access, and inspect job_status until terminal. Set result in Python for structured output. Failed Python may partially change the scene. Render returns a job; use view_image on its artifact. Save and download projects before stopping. No local Blender is required.",
)
controller = Controller()


def tool(fn):
    """Keep blocking SDK calls off the MCP event loop."""

    @wraps(fn)
    async def wrapped(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    return mcp.tool()(wrapped)


@tool
def create_session(
    project: str,
    gpu: Literal["T4", "L4", "A10G", "L40S"] | None = None,
    lifetime_seconds: int = 3600,
    idle_seconds: int = 900,
    mode: Literal["headless", "gui"] = "headless",
    restore_revision: Annotated[str, Field(pattern="^[0-9a-f]{32}$")] | None = None,
    new_workspace: bool = False,
    workspace_id: str | None = None,
) -> dict:
    """Start or reconnect to a project. GPU resources incur charges until stopped.

    First creation builds a Blender image and can take several minutes. Existing
    projects restore their last checkpoint. Set restore_revision to recover an older
    checkpoint at startup if the latest one is unusable; stop any active session first.
    new_workspace=true creates a new independent Blender/Sandbox/Volume, even for the
    same project label. Returned workspace_id can reopen that exact workspace later.
    Without either option, the default project workspace is reused. Agent spawning,
    scheduling and aggregation are entirely the caller's responsibility.
    """
    return controller.create(
        project,
        gpu,
        lifetime_seconds,
        idle_seconds,
        mode,
        restore_revision,
        new_workspace=new_workspace,
        workspace_id=workspace_id,
    )


@tool
def list_sessions() -> list[dict]:
    """List locally recorded sessions. Records can include ended sandboxes."""
    return controller.list()


@tool
def session_status(session_id: str) -> dict:
    """Inspect remote worker, checkpoint and recent jobs; refresh user activity."""
    return controller.rpc(session_id, "status")


@tool
def get_logs(session_id: str, max_bytes: Annotated[int, Field(ge=1, le=64000)] = 16000) -> dict:
    """Read the tail of Blender's native log for errors not captured in Python stdout."""
    return controller.rpc(session_id, "logs", max_bytes=max_bytes)


def inspection(session_id, kind, params):
    job = controller.submit(session_id, kind, params)
    return controller.wait(session_id, job["id"])


@tool
def get_environment(session_id: str) -> dict:
    """Inspect actual Blender/Python versions, enabled add-ons, devices and GUI capabilities."""
    return inspection(session_id, "environment", {})


@tool
def inspect_object(session_id: str, name: str) -> dict:
    """Inspect mesh counts, transforms, materials, modifiers, constraints, rig and animation action."""
    return inspection(session_id, "object", {"name": name})


@tool
def inspect_nodes(
    session_id: str,
    name: str,
    owner_type: Literal["material", "group", "world", "scene"] = "material",
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
) -> dict:
    """Inspect shader, geometry or compositor nodes, sockets and links with pagination."""
    return inspection(session_id, "nodes", dict(name=name, owner_type=owner_type, offset=offset, limit=limit))


@tool
def inspect_dependencies(
    session_id: str, offset: Annotated[int, Field(ge=0)] = 0, limit: Annotated[int, Field(ge=1, le=500)] = 100
) -> dict:
    """Report packed/missing assets and resolved paths. Reports coverage limits explicitly."""
    return inspection(session_id, "dependencies", dict(offset=offset, limit=limit))


@tool
def describe_api(
    session_id: str,
    category: Literal["operator", "type"] = "operator",
    name: str = "",
    query: str = "",
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
) -> dict:
    """Discover installed operators or inspect real RNA property schemas, enums and defaults.

    Omit name and use query to search operators, e.g. export or mesh. Give an exact
    operator (mesh.primitive_cube_add) or RNA type (Object) for its schema. No eval.
    """
    return inspection(
        session_id, "api", dict(category=category, name=name, query=query, offset=offset, limit=limit)
    )


@tool
def run_operator(
    session_id: str,
    name: str,
    properties: dict | None = None,
    context: Literal["CURRENT", "VIEW_3D"] = "CURRENT",
    checkpoint: bool = True,
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 120,
) -> dict:
    """Run an installed Blender operator with structured arguments and optional GUI context.

    Use describe_api first. Supports modeling, modes, import/export, baking and more,
    as available in this build. EXEC_DEFAULT must report FINISHED. Mutations save by
    default. Prefer execute_python for custom logic, data APIs or modal UI interactions.
    """
    return controller.submit(
        session_id,
        "operator",
        dict(name=name, properties=properties or {}, context=context, checkpoint=checkpoint),
        timeout_seconds,
    )


@tool
def execute_python(
    session_id: str,
    code: str,
    checkpoint: bool = True,
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 120,
) -> dict:
    """Queue arbitrary Python in persistent Blender. Full bpy and remote OS access.

    Assign a JSON-serializable value to result. Variables are per call; bpy scene
    state persists. Successful calls save a checkpoint by default. A timeout or
    running-job cancellation restarts Blender and loses unsaved changes.
    """
    return controller.submit(session_id, "python", {"code": code, "checkpoint": checkpoint}, timeout_seconds)


@tool
def inspect_scene(
    session_id: str, offset: Annotated[int, Field(ge=0)] = 0, limit: Annotated[int, Field(ge=1, le=500)] = 100
) -> dict:
    """Inspect Blender version, frame and object transforms with pagination."""
    job = controller.submit(session_id, "inspect", {"offset": offset, "limit": limit})
    return controller.wait(session_id, job["id"])


@tool
def job_status(session_id: str, job_id: str, wait_seconds: Annotated[int, Field(ge=0, le=30)] = 0) -> dict:
    """Read a job, including results, stdout and tracebacks. Optionally wait up to 30s."""
    return controller.wait(session_id, job_id, wait_seconds)


@tool
def cancel_job(session_id: str, job_id: str) -> dict:
    """Request cancellation. Running jobs restart Blender from its last checkpoint."""
    return controller.rpc(session_id, "cancel", job_id=job_id)


@tool
def render(
    session_id: str,
    width: Annotated[int, Field(ge=64, le=4096)] = 512,
    height: Annotated[int, Field(ge=64, le=4096)] = 512,
    samples: Annotated[int, Field(ge=1, le=1024)] = 16,
    device: Literal["CPU", "CUDA", "OPTIX"] = "CPU",
    frame: int | None = None,
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)] = 300,
) -> dict:
    """Queue a Cycles camera render. Requires a camera; GPU requires a GPU session.

    Returns a job ID. On success view_image retrieves the PNG. The live session
    serializes renders with edits. Render settings are restored after the job.
    """
    return controller.submit(
        session_id,
        "render",
        dict(width=width, height=height, samples=samples, device=device, frame=frame),
        timeout_seconds,
    )


@tool
def control_viewport(
    session_id: str,
    view: Literal["FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "ISO", "CAMERA"] | None = None,
    shading: Literal["WIREFRAME", "SOLID", "MATERIAL", "RENDERED"] | None = None,
    select: list[str] | None = None,
    frame_selected: bool = False,
    target: tuple[float, float, float] | None = None,
    distance: Annotated[float, Field(gt=0, le=100000)] | None = None,
    rotation: tuple[float, float, float] | None = None,
    overlays: bool | None = None,
    frame: int | None = None,
) -> dict:
    """Control the live Blender GUI: select/frame objects, orbit, pan, zoom and shade.

    Requires a GUI session. Rotation is XYZ Euler angles in degrees; target pans
    the view center. Changes persist in the live viewport. Capture with viewport_screenshot
    after this job succeeds. Software graphics rendering happens remotely.
    """
    params = dict(
        view=view,
        shading=shading,
        select=select,
        frame_selected=frame_selected,
        target=target,
        distance=distance,
        rotation=rotation,
        overlays=overlays,
        frame=frame,
    )
    job = controller.submit(session_id, "viewport", params)
    return controller.wait(session_id, job["id"])


@tool
def viewport_screenshot(session_id: str) -> list:
    """Capture the actual Blender GUI window and return an inline image plus its artifact path.

    Requires a GUI session. This includes viewport overlays, selection and editor UI.
    It is separate from a Cycles camera render. If the job is still queued, poll its ID
    and retrieve the resulting artifact with view_image.
    """
    from mcp.types import TextContent
    import json

    job = controller.submit(session_id, "screenshot", {})
    job = controller.wait(session_id, job["id"])
    content = [TextContent(type="text", text=json.dumps(job))]
    if job["status"] == "succeeded":
        content.append(
            Image(data=controller.image(session_id, job["result"]["path"]), format="png").to_image_content()
        )
    return content


@tool
def view_image(session_id: str, path: str) -> Image:
    """Return a workspace PNG as inline MCP image content (maximum 10 MB)."""
    return Image(data=controller.image(session_id, path), format="png")


@tool
def save_project(session_id: str, path: str | None = None) -> dict:
    """Queue a durable checkpoint. Optional new workspace path exports a .blend for download."""
    return controller.submit(session_id, "save", {"path": path})


@tool
def open_project(session_id: str, path: str) -> dict:
    """Replace the live scene with an uploaded .blend; disables embedded script auto-run.

    Save current edits first. Successful open creates a new checkpoint.
    """
    return controller.submit(session_id, "open", {"path": path})


@tool
def list_checkpoints(session_id: str) -> list[dict]:
    """List saved revisions that can be restored, newest first."""
    return controller.rpc(session_id, "checkpoints")


@tool
def restore_checkpoint(session_id: str, revision: str) -> dict:
    """Replace the live scene with a saved revision. Save current edits first."""
    return controller.submit(session_id, "restore", {"revision": revision})


@tool
def list_files(
    session_id: str,
    path: str = "",
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=1000)] = 1000,
) -> list[dict]:
    """List up to 1000 workspace files, excluding internal state."""
    return controller.rpc(session_id, "files", path=path, offset=offset, limit=limit)


@tool
def upload_file(session_id: str, local_path: str, remote_path: str) -> dict:
    """Upload a local file to a new relative workspace path. Never overwrites."""
    return controller.upload(session_id, local_path, remote_path)


@tool
def download_file(session_id: str, remote_path: str, local_path: str) -> dict:
    """Download a workspace file to a new local filename. Maximum size 5 GB."""
    return controller.download(session_id, remote_path, local_path)


@tool
def stop_session(session_id: str, discard_unsaved: bool = False) -> dict:
    """Save a checkpoint and terminate compute. Project volume and revisions persist.

    Drains accepted jobs before saving; rejects new jobs while draining. A pending
    save returns stopped=false and its job ID. Retry after it finishes. Setting
    discard_unsaved=true terminates immediately and loses unsaved edits.
    """
    return controller.stop(session_id, discard_unsaved)


def main():
    parser = argparse.ArgumentParser(description="Remote Blender MCP server")
    parser.add_argument("--list-sessions", action="store_true")
    args = parser.parse_args()
    if args.list_sessions:
        import json

        print(json.dumps(controller.list(), indent=2))
    else:
        mcp.run(transport="stdio")
