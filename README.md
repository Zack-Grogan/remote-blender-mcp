<img src="assets/icon.svg" width="64" height="64" alt="">

# Remote Blender MCP

Control Blender on Modal from Codex, Claude Code, OpenCode, or another MCP client. The local Python server manages remote compute and transfers files. Blender runs remotely in a persistent process with full `bpy` access.

## Setup

The local prototype supports macOS and Linux.

Install [uv](https://docs.astral.sh/uv/) and authenticate with Modal:

```sh
uv sync --locked
uv run modal setup
```

Install the Codex configuration for this checkout:

```sh
uv run python scripts/setup_codex.py
```

This writes only the project's `.codex/config.toml`. It preserves other settings, uses the installed `uv` path, and forwards the optional Modal/profile and session-registry environment variables. Run it again after moving the checkout. Start a new Codex session in this trusted project to load the MCP. The configuration approves this server's tools, including arbitrary remote Python. Remove `default_tools_approval_mode` if you want Codex to ask before calls.

The generated entry has this form:

```toml
[mcp_servers.remote_blender]
command = "uv"
args = ["run", "--locked", "--directory", "/absolute/path/remote-blender-mcp", "remote-blender-mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 600
default_tools_approval_mode = "approve"
```

The first session builds a Blender image and can take several minutes. Provisioning has a 180-second deadline, followed by a bounded worker-readiness check. A stalled allocation is cancelled only when its ownership tag matches the request. Different projects provision independently. The longer Codex tool timeout accommodates startup. Renders and Python operations return job IDs promptly. Use `job_status` to retrieve their results.

For clients that use JSON configuration:

```json
{
  "mcpServers": {
    "remote_blender": {
      "command": "uv",
      "args": ["run", "--locked", "--directory", "/absolute/path/remote-blender-mcp", "remote-blender-mcp"]
    }
  }
}
```

Use an absolute path to `uv` if the client cannot find it. The server uses your existing Modal profile; it does not copy Modal credentials into Blender. Client configuration and image rendering behavior must be checked in the client you use.

## Compute selection

CPU is the default for modeling, rigging, animation editing, scene setup, nodes, routine inspection and small test renders. Codex should consider a GPU only for planned heavy animation rendering or expensive final renders when the supported backend and expected time/cost justify it. Being a 3D task, using GUI mode or spawning multiple agents is not enough to justify a GPU.

Use the [Codex CPU/GPU decision matrix](AGENTS.md#cpugpu-decision-policy). Codex makes this choice per item and stage, records a short rationale, and stops GPU compute when the justified render stage ends. Allocation and render-device selection are separate: reserving a GPU does not automatically make Blender use it. No workload classifier or GPU-use restriction is implemented in the MCP.

## First workflow

1. Call `create_session` with a project name such as `demo`. Use `mode="gui"` for viewport control or `mode="headless"` for batch work. The default uses CPU and headless mode. Leave `gpu` unset for CPU; choose a supported GPU only when the decision matrix justifies the planned render stage. GPU availability depends on your Modal account and capacity.
2. Call `inspect_scene` with the returned `session_id`.
3. Call `execute_python`, then `job_status` until the job succeeds or fails. Assign a JSON-serializable value to `result` to return data.
4. Call `render`, then inspect its job. Call `view_image` with the resulting relative PNG path for inline visual feedback.
5. Call `save_project` with a new path such as `exports/scene.blend`, wait for success, and use `download_file` to retrieve it.
6. Call `stop_session` to save the scene and terminate compute. Saved project data remains in its Modal Volume.

Example Blender code:

```python
import bpy

bpy.ops.mesh.primitive_uv_sphere_add(location=(2, 0, 1))
sphere = bpy.context.object
sphere.name = "MySphere"
material = bpy.data.materials.new("Blue")
material.diffuse_color = (0.02, 0.15, 0.8, 1.0)
sphere.data.materials.append(material)
result = {"name": sphere.name, "objects": len(bpy.data.objects)}
```

A fresh project includes Blender's default cube, camera and light. `render` uses Cycles and the scene's camera. Set `device="CUDA"` or `"OPTIX"` for a GPU session; unavailable devices produce an error instead of silent CPU fallback. Specialized modeling, geometry nodes, materials, animation, rigging, simulation, import/export and add-on inspection are available through Python, subject to the pinned Blender build and background context.

## Remote viewport

Create a session with `mode="gui"`. The sandbox runs the official Blender 4.5.0 application on a private 1280 × 800 virtual display. `control_viewport` selects objects, frames selection, changes axis views, orbits with XYZ angles, pans with a target point, zooms with distance, changes shading and sets the timeline frame. Auto-framing evaluates the requested animation frame before calculating bounds. It checks selected bounding-box corners and armature endpoints against the real viewport region and adds a screen-space margin. Explicit target or distance values override auto-framing and can intentionally crop the view. `viewport_screenshot` returns the actual desktop framebuffer as an inline image, including Blender's viewport, selection, overlays and editors. Poll a pending screenshot job and retrieve its PNG with `view_image`.

Example Codex request:

> Create a GUI session named my-blender-project. Add a sphere, select it, frame it in an isometric solid view, and show the viewport screenshot. Then switch to wireframe and inspect the result. Keep the project checkpointed.

The viewport uses Mesa software OpenGL on the remote CPU. This avoids requiring a local graphics context and does not claim hardware-accelerated viewport drawing. A GPU session can still use CUDA/OptiX for Cycles camera rendering. GUI commands run on Blender's main thread between UI redraws. The screenshot is a tool-requested image of the live GUI; no desktop video stream or mouse/keyboard forwarding is exposed.

## Tools

| Tools | Purpose |
| --- | --- |
| `create_session`, `list_sessions`, `session_status`, `stop_session` | Manage remote compute and reconnect using the local registry. |
| `execute_python`, `run_operator` | Full Python access or structured operator execution, including modeling, import/export and baking. |
| `inspect_scene`, `inspect_object` | Scene transforms, mesh counts, modifiers, materials, constraints, rig and animation action. |
| `inspect_nodes` | Shader, Geometry Nodes, world and compositor graphs, sockets and links. |
| `describe_api` | Search installed operators and inspect actual RNA schemas, enums and defaults. |
| `get_environment`, `get_logs`, `inspect_dependencies` | Discover runtime capabilities, diagnose errors and check asset references. |
| `control_viewport`, `viewport_screenshot` | Control and inspect the actual remote GUI viewport. Requires GUI mode. |
| `render`, `view_image` | Create camera renders and return inline PNG images. |
| `job_status`, `cancel_job` | Retrieve results, tracebacks and logs; stop queued or running work. |
| `save_project`, `open_project` | Save checkpoints, export a `.blend`, or open an uploaded project. |
| `list_checkpoints`, `restore_checkpoint` | List and restore saved scene revisions. |
| `upload_file`, `download_file`, `list_files` | Transfer files with checksum verification and explicit relative paths. |

## State and recovery

Each workspace uses its own named Modal Volume mounted at `/workspace`. The default workspace is addressed by project name. `new_workspace=true` creates an independent workspace even when another session has the same project label. Internal jobs and checkpoints live in `/workspace/.rbm`. Assets and exports use ordinary project paths. Transfers reject existing destination files. Upload dependencies explicitly; packing images does not include every possible dependency.

Successful Python operations save checkpoints by default. Set `checkpoint=false` for inspection or temporary edits. The Python namespace is new on each call; Blender scene data persists. Python errors can leave partial edits. Cancellation of a running job or a job deadline kills Blender and restores the last completed checkpoint. Queued work is failed after a worker restart so it cannot silently execute against an older scene. Inspect the restored state before resubmitting it. Job status records that unsaved changes were lost. Restoring a checkpoint or opening a project replaces the live scene.

The supervisor saves and stops after the configured user-idle period (15 minutes by default), once queued work is complete. Session status queries count as activity; job polling does not. An active job has a separate deadline. The default maximum session lifetime is one hour. A hard lifetime expiry or host failure can lose changes since the last completed checkpoint.

`stop_session` places a barrier after already accepted jobs and rejects later jobs while draining. If the final save is pending after approximately 30 seconds, it returns `stopped:false`, `draining:true` and the save job ID. Poll that job, then retry stopping; the retry reuses the same save. Failed saves reopen submission so the scene can be repaired. `discard_unsaved=true` provides emergency termination and loses unsaved edits. For a default workspace, create another session with the same project name to load the last checkpoint after termination. For an independent workspace, supply its returned `workspace_id` when reopening it. `session_status` and `list_checkpoints` also work after compute ends. If the latest checkpoint is unusable, create a session with `restore_revision` set to an older checkpoint ID. New checkpoints include checksum manifests; startup refuses a checksum mismatch. One named sandbox per workspace prevents duplicate attachment to that workspace in the same Modal App; independent workspaces may run concurrently under one project label. Changing the GPU profile or switching between headless and GUI modes requires stopping the current session first.

Local session records live in `~/.local/state/remote-blender-mcp`; set `RBM_STATE_DIR` to change that location. Set `RBM_MODAL_APP` to change the default Modal App name. Default Volumes use `rbm-<project>` names; independent Volumes use `rbm-i_<workspace UUID hex>`. Both retain data after compute stops. Delete retained data only through an explicit storage-management action.

## Prototype scope

The headless image uses Python 3.11 and `bpy==4.5.0`. GUI mode adds the official Blender 4.5.0 Linux archive, verified against its published SHA-256, and Xvfb. The lockfile pins the local dependency versions. Operations are serialized within each session, including renders. The prototype provides headless scene control, camera renders and live GUI viewport control with screenshots. Remote desktop input/video streaming, independent render workers, automatic dependency synchronization and asset-provider integrations are future work.

Arbitrary Python can access the remote OS and the project's mounted data. It is intended for a trusted local harness. The sandbox has no public port (X11 TCP listening is disabled), no injected user secrets, and outbound networking is disabled. Assets and add-ons that require network downloads must be staged explicitly or added to the image. Local upload/download tools have access to paths supplied by the harness. Do not expose the stdio server as an unauthenticated shared service.

CPU sessions request one physical core and 2 GiB RAM, with limits of four physical cores and 8 GiB RAM. GPU sessions also reserve the selected GPU. Modal charges for running resources; terminate sessions when finished. Checkpoint and artifact storage persists separately.

## Validation

```sh
uv run pytest -q
uv run ruff check .
```

The live acceptance script starts billable Modal resources and stops its session when finished:

```sh
uv run python scripts/validate_live.py
RBM_TEST_GPU=L4 uv run python scripts/validate_live.py
```

It writes artifacts and a report under `.validation/`. See [prototype validation](docs/prototype-validation.md) for observed results and limits, and [research and design](docs/research-and-design.md) for source research.

Actual Codex and GUI acceptance tests use an existing GUI session that you own:

```sh
uv run python scripts/validate_codex.py <session_id>
uv run python scripts/validate_viewport.py <session_id>
```

The Codex script uses your existing Codex login through a temporary authentication reference and an isolated configuration containing project trust. It loads this project's MCP entry and records real tool calls and model image inspection. It does not load home-level MCP servers or plugins. Both scripts leave the supplied session running; stop it with `stop_session` when finished.

## Command coverage and debugging

There are 26 MCP tools. Use `get_environment` to identify the actual Blender build, enabled add-ons, devices and GUI mode. Search `describe_api` for the installed operator before invoking it through `run_operator`. For example, search `export`, inspect `export_scene.gltf`, create a workspace export directory through Python, then run the operator with the discovered arguments. `CURRENT` and `VIEW_3D` execution contexts are supported. An operator must report `FINISHED`; a failed poll or cancelled operator becomes an explicit failed job.

For work beyond a structured tool, `execute_python` exposes the full remote `bpy` API. This covers arbitrary geometry, nodes, animation, rigging, simulations, editor overrides and supported add-ons. It does not make a missing add-on available automatically. Dynamic enum choices are resolved from registered callbacks when possible; unresolved or truncated choices are marked with `items_complete:false`. Dynamic defaults can be unavailable in raw RNA, so choose a listed enum identifier instead of blindly copying an empty default.

Use `inspect_object` and `inspect_nodes` to verify changes, then inspect a viewport image or camera render. `inspect_dependencies` reports its coverage limit and is not a complete dependency closure. `get_logs` reads native Blender output while the session runs; ended sessions return persisted worker diagnostics, including startup error details. Successful inspection does not prove that every add-on or simulation engine has been tested.

Requests use stdin rather than command-line arguments. Remote execution-control requests have a 35-second deadline; if a response is lost, the operation's outcome may be unknown. Inspect session/job state before retrying a mutation. Structured job results are limited to 1 MB; write larger data to workspace files. File listings are paginated, and internal state and symbolic links are excluded.

Additional billable failure-path checks:

```sh
uv run python scripts/validate_lifecycle.py
uv run python scripts/validate_recovery.py
```

See [debug audit and coverage](docs/debug-audit.md) for the verified tool coverage and regression results. Existing sandbox runtimes are immutable; stop them and start fresh sessions after a server update. Restart Codex to reload expanded tool schemas.

## Multiple independent renders

Codex owns splitting the request, spawning subagents, scheduling, retries and aggregation. Project guidance is in [AGENTS.md](AGENTS.md). The MCP does not interpret the user request or provide an agent scheduler, task broker, worker-assignment service or batch ledger.

The resource primitive is:

```text
create_session(project="my-project", new_workspace=true)
```

Each call creates a fresh Blender process, Sandbox, persistent Volume, scene, queue and checkpoint namespace. Two callers may use the same project label and the same remote filenames without sharing state. Provisioning locks are scoped to the workspace, so one workspace does not serialize another's startup. Resource identifiers include a UUID namespace distinct from ordinary project names.

The response includes `session_id`, `workspace_id`, `sandbox_id` and `volume`. A worker uses only its own session ID. Codex assigns a unique local output directory to each item, downloads and checks its files before stopping compute, and combines the reported results. The existing download tool refuses to overwrite a local file; output layout remains Codex's choice.

Reopen an independent workspace after termination with:

```text
create_session(project="my-project", workspace_id="<returned workspace UUID>")
```

Add `restore_revision` to select one of that workspace's checkpoints. Do not combine `new_workspace=true` with an existing `workspace_id` or recovery revision. Omitting both isolation options preserves the existing default-workspace behavior.

This is resource separation for a trusted harness, not access control between hostile tenants. Agent count and resource budgets remain the harness's responsibility. Each active sandbox incurs its own Modal charges and retains its saved Volume after compute stops.
