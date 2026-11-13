# Remote Blender MCP: research and initial design

Research date: 2026-11-12. This document records a proposed design, not an implemented or validated system. Product behavior and SDK interfaces can change; pin and test the versions used by the implementation.

## Objective

Give Codex and other local MCP clients broad control over Blender running on Modal. Keep the interaction close to a local workflow: a persistent scene, quick inspection and iteration, visual feedback, and explicit access to project files and results. Move Blender's compute and memory load off the local machine. Network transfer, cloud startup, GPU cost, and remote storage remain real constraints.

## Research findings

### Modal Sandboxes

- A local Python process can look up an App, create a Sandbox, run commands with `exec`, and terminate it. Sandboxes support resource selection and optional idle and lifetime timeouts. Requested resources form a billing floor; CPU and memory bursting can increase charges. GPU Sandboxes can be preempted. [Sandboxes](https://modal.com/docs/guide/sandboxes), [resources and pricing](https://modal.com/docs/guide/sandbox-resources), [SDK reference](https://modal.com/docs/sdk/py/latest/Sandbox).
- The current filesystem API can copy files between the local machine and a running Sandbox and inspect remote files. Modal documents reads up to 5 GB and writes of any size. Large projects and repeat access favor mounted storage rather than repeated copies. [Filesystem access](https://modal.com/docs/guide/sandbox-files).
- Volumes are mounted into Sandboxes. Background commits and a final commit on termination do not make every write instantly durable; the documentation describes explicit `sync` for v2 and reloads for visibility of other writers. Concurrent writers require a deliberate ownership policy. [Filesystem access](https://modal.com/docs/guide/sandbox-files).
- Filesystem snapshots do not include mounted Volumes or running memory. Exit snapshots are beta and do not reliably cover GPU preemption. Memory snapshots have constraints including no GPU support and termination during snapshot. None replaces saving and verifying project checkpoints. [Snapshots](https://modal.com/docs/guide/sandbox-snapshots), [exit snapshots](https://modal.com/docs/guide/sandbox-exit-snapshots).
- Modal recommends Sandbox Connect Tokens for HTTP and WebSocket access. Raw forwarded TCP ports are publicly reachable and need their own authentication. A first implementation can avoid inbound ports by using Sandbox execution and filesystem access; test its latency before selecting a persistent transport. [Networking](https://modal.com/docs/guide/sandbox-networking).

### Blender capabilities and constraints

- Blender's Python API provides broad access to scene data and operations. Arbitrary `bpy` execution is the essential escape hatch for modeling, materials, geometry nodes, rigging, animation, simulations, import/export, rendering and add-ons, subject to the installed Blender build and execution context. Curated tools should make common tasks easier without pretending to cover the full API.
- Blender supports background command-line rendering without a graphical display. Arguments are order-sensitive. A background render or generated camera view is not an interactive viewport screenshot; features needing a window, GPU drawing context, or specific editor context must be tested separately. [Command-line rendering](https://docs.blender.org/manual/en/latest/advanced/command_line/render.html).
- Blender warns that Python threads are not generally supported and can cause crashes. Run Blender API operations on its main thread and serialize scene mutations. A bridge must validate how its event loop and timers work in the selected headless and GUI modes. [Threading gotchas](https://docs.blender.org/api/main/info_gotchas_threading.html).
- GPU availability alone does not prove that Blender selected the GPU or that a particular render engine works. Record the detected device, backend and effective render settings, and test actual renders. [Command-line arguments and Cycles options](https://docs.blender.org/manual/en/latest/advanced/command_line/arguments.html).

### Existing AI integrations

- [Blender Lab MCP Server](https://www.blender.org/lab/mcp-server/) is published under Blender Lab; direct retrieval was blocked during the audit, as detailed below. Evaluate its actual protocol, supported Blender versions, headless behavior, license, and security before choosing it as a bridge; its existence does not establish that it meets the remote use case.
- [BlenderMCP](https://github.com/ahujasid/blender-mcp) demonstrates scene inspection, arbitrary Python and external asset or generation integrations. [blendmcp](https://github.com/owenpkent/blendmcp) highlights screenshots and tracebacks, while [Blender Bridge](https://github.com/Blender-Bridge/blender-bridge) combines inspection, operations and visual review. These are useful interaction patterns, not evidence that their protocols, add-ons or integrations are interchangeable or suitable for a public remote endpoint.
- The design opportunity is a complete remote workflow: persistent state, visible inspection, explicit file custody, recoverable changes and compute management. Asset search and AI generation are optional adapters, not prerequisites for full Blender Python access. Respect each provider's license and credentials.

### MCP compatibility

Use the official [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk) and start with local stdio so the harness launches a small local process. The SDK's [migration guide](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md) describes version-sensitive API changes. Pin a tested release and verify real Codex, Claude Code and OpenCode behavior, including image results, tool errors and long-running calls. Do not assume every client supports every MCP feature.

## Proposed architecture

```text
Codex / Claude Code / OpenCode
       | local MCP stdio
Python MCP server + session registry
       | Modal SDK: lifecycle, execution, file transfer
       v
Per-project Modal Sandbox
  bridge / command queue -> Blender main thread -> live scene
  project mount + checkpoint files + artifact staging
       |
       +--> separate CPU/GPU render sandboxes from a saved revision
```

The local server is the control plane. It holds Modal authentication through the user's normal secure configuration, manages sessions, and transfers selected files. It must not embed credentials in project files or logs. The remote Blender process owns the live scene. A single writer queue assigns operation IDs and serializes mutations; independent render workers receive a specific immutable checkpoint and write to separate output paths. A controller restart should reconnect by recorded sandbox identity if it is still alive; after loss, restore the latest verified saved checkpoint into a new session. Unsaved in-memory changes can be lost.

Start with a persistent **headless** Blender process and a small bridge, provided a proof confirms reliable main-thread dispatch. If it cannot provide a required interaction, add a separate GUI-capable mode with a virtual display and verify viewport capture, graphics drivers and remote interaction end to end. Do not promise live desktop interaction from headless renders. The protocol between the controller and Blender should be versioned and private to the sandbox; avoid exposing an unauthenticated code-execution port.

## Capability surface

Keep tools discoverable and composable. Names below are a proposed contract, not existing APIs.

| Area | Proposed operations and behavior |
| --- | --- |
| Session | Create, list, inspect, reconnect, stop; report state, versions, resources, uptime, configured timeout and estimated cost inputs. Stop must say whether unsaved changes exist. |
| Project and files | Upload/download explicit paths, inspect dependencies, open/save `.blend`, pack or resolve linked assets, list artifacts and export formats. Report missing assets and transfer progress. Never silently overwrite a local file. |
| Scene inspection | Scene, objects, hierarchy, transforms, bounds, collections, materials, nodes, modifiers, animation, render settings and dependency reports; paginate large results. |
| Broad control | Execute Blender Python in the live session, return stdout, structured results and tracebacks. Provide selected structured helpers for frequent edits; do not restrict `bpy` to a small fixed menu. |
| Visual feedback | Camera render, multiple diagnostic views and animation contact sheets returned as MCP images where supported, with downloadable full-resolution artifacts. Report actual render mode and camera. Real viewport capture is a separately tested capability. |
| Jobs | Start, inspect, cancel and retrieve renders, simulations, imports and exports. Record revision, status, errors and output paths. A client timeout must not imply a job stopped. |
| Recovery | Save named checkpoints, list and restore them; report last durable revision and whether the live scene is dirty. Preserve prior revisions until explicitly removed. |
| Environment | Report Blender, add-on, Python, GPU and feature availability. Optional controlled add-on or dependency installation must be reproducible and recorded. |

Arbitrary Python has the authority of the remote process, including access to its mounted files and permitted network. Isolation protects the local machine only to the extent that local files and credentials are not exposed to that process. Scope uploads and mounts to the project, avoid broad secrets, restrict egress where practical, and treat imported assets, scripts and tool output as untrusted data. User-configurable resource limits and clear destructive-operation semantics matter as much as the sandbox boundary.

## Project and state model

A project has a local root and an explicit remote workspace. Upload an inventory and dependency manifest with paths, sizes and hashes; preserve relative paths and identify absolute paths, linked libraries, fonts, caches and external textures. Record a revision only after Blender saves the file, storage is committed as appropriate, and the saved bytes can be read back and checked. Distinguish local copy, remote saved checkpoint, in-memory scene and render output. Use immutable revision IDs for render jobs and compare hashes before downloading over a changed local file. Initial synchronization should be explicit and manifest-based; automatic bidirectional synchronization needs conflict detection and must not be implied by a volume mount.

## Validation sequence

1. Pin Modal SDK, MCP SDK and a Blender release; record image build inputs and license requirements. Prove sandbox create, reconnect, transfer, terminate and cost reporting with a small project.
2. Prove a persistent headless Blender bridge across multiple commands, errors, file loads and saves. Verify main-thread execution, ordered mutations and recovery after process or sandbox loss.
3. Run an end-to-end local Codex MCP session: upload a `.blend` and texture, inspect, edit with Python, produce diagnostic images, save, verify and download the result. Reopen the downloaded file and check scene and dependencies. Repeat basic compatibility with Claude Code and OpenCode.
4. Test CPU and GPU renders with the actual selected backend; measure cold start, warm call latency, transfer time, render time and cost. Test cancellation, timeout, crash, full storage, preemption and concurrent edit conflicts.
5. Prototype GUI/viewport mode only after proving display and graphics requirements. Compare real viewport screenshots with camera renders and document which operations each mode supports.
6. Evaluate Blender Lab and community bridges against the tested contract, including license and maintenance. Adopt only if their behavior and security fit; otherwise maintain a minimal bridge.

Acceptance means a user can recover a verified project and its dependencies after the remote session ends, see meaningful images during iteration, and perform broad `bpy` operations through a real local harness. It does not mean every Blender UI feature or third-party add-on is supported without a tested GUI environment.

## Open decisions

- Blender release and supported add-on set; stable version versus newer capabilities.
- Default CPU/GPU resource profile, cost ceiling, idle policy and region, informed by measured latency and pricing.
- Project storage retention, quotas and whether a dedicated Volume per project is preferable to scoped subdirectories.
- Whether interactive remote desktop access is a product requirement or whether diagnostic renders satisfy the first workflow.
- Which asset providers, generation services and render engines to support after the core loop works.

## Validation audit and expanded findings

Audit date: 2026-11-12. **Validated** below means supported by retrieved primary documentation. It does not mean executed successfully in this project. **Proposed** means a design choice. **Unresolved** means more evidence or an integration test is required. Search-index excerpts were used where direct retrieval failed; those limits are stated explicitly.

### Audit of the original findings

| Original claim | Audit result and correction |
| --- | --- |
| Local Python can create, execute in and terminate a Sandbox | Validated in Modal's Sandbox guide and Python SDK. `Sandbox.from_id` supports later retrieval of a running Sandbox. This is reconnection, not resurrection after termination. |
| Resource selection and timeouts exist | Validated. The guide documents a five-minute default lifetime and a configurable lifetime up to 24 hours. Set the lifetime explicitly. |
| Billing follows requested resources throughout the lifetime | Incomplete. Requests are a billing floor; CPU and memory are billed at `max(request, actual)` and can burst. Resource limits and requests are different controls. Do not estimate the total bill from reservations alone. |
| GPU Sandboxes can be preempted | Validated by the exit-snapshot documentation. Checkpoints remain necessary even with automatic exit snapshots. |
| Filesystem API supports local transfers and reads up to 5 GB | Validated. This API limit matters for large `.blend`, cache and video outputs; choose a separate tested transfer path for larger files. |
| Volumes need explicit durability and visibility handling | Validated. The v2 `sync` example checks process completion and return code. Never label a checkpoint durable after merely starting that command. |
| Filesystem snapshots exclude mounted volumes; memory snapshots cannot use GPUs | Validated in the snapshot guide. The termination restriction belongs to memory snapshots, not every snapshot type. |
| Connect Tokens are recommended for HTTP/WebSocket access | Validated in the networking guide. They authorize transport access; they do not implement our project ownership or operation policy. |
| Broad `bpy` access enables most Blender workflows | Supported by Blender's API index, with context and build constraints. Broad API access does not prove support for every editor operation or add-on. |
| Background rendering requires no desktop | Supported by Blender's rendering manual. This does not prove Eevee, viewport drawing or offscreen GPU context availability in our image. |
| Main-thread operations and serialized mutations are needed | Supported by Blender's threading warning. Merely moving `bpy` calls to the main thread does not establish that a permanent Python network thread inside Blender is safe. Prefer a separate supervisor process. |
| Existing tools demonstrate useful workflows | Their maintainers document these features. We have not reproduced their behavior. Blender Bridge is a Windows product documentation/release repository, not an established reusable Linux bridge implementation. |
| Local stdio is appropriate for Codex | Validated as a supported transport. Cross-client image behavior and compatibility remain integration tests. |
| Persistent headless session plus separate render workers | Proposed architecture. No source proves our bridge, lifecycle, graphics or recovery implementation. |
| Local-feeling operation | A product objective. No latency, cost or user-experience measurement exists yet. |

Primary references: [Sandbox lifecycle](https://modal.com/docs/guide/sandboxes), [resources](https://modal.com/docs/guide/sandbox-resources), [filesystem](https://modal.com/docs/guide/sandbox-files), [snapshots](https://modal.com/docs/guide/sandbox-snapshots), [exit snapshots](https://modal.com/docs/guide/sandbox-exit-snapshots), [networking](https://modal.com/docs/guide/sandbox-networking), [Blender API index](https://docs.blender.org/api/current/), [Codex MCP](https://developers.openai.com/codex/mcp).

### Session lifetime and user-idle policy

Modal considers an active exec command, stdin writes or an open tunneled TCP connection activity. A persistent connection or a Blender process launched through a long-running exec can therefore defeat the intended user-idle shutdown. Maintain a separate timestamp for meaningful user operations. A supervisor should drain jobs, save a checkpoint, confirm storage completion, and terminate when the configured user-idle policy permits. Active jobs need their own deadline. This policy is proposed; test it with each transport. See [idle timeout semantics](https://modal.com/docs/guide/sandboxes).

Store sandbox ID, project ID, image identity and last checkpoint independently of the local MCP process. On reconnect, inspect the remote process and scene revision before accepting edits. A stale registry entry must produce an explicit expired-session result. Named sandboxes can help discovery but do not replace project ownership or revision checks. See [Sandbox reference](https://modal.com/docs/sdk/py/latest/Sandbox).

### A stronger Blender execution design

Use a separate supervisor process for networking, downloads, job state and process control. For the headless prototype, evaluate a synchronous command-reading loop running inside Blender's main Python execution. That can preserve state without relying on a GUI event loop or permanent Python background threads. For GUI mode, evaluate main-thread timer dispatch with interprocess communication. Both are proposals that need tests across `.blend` loads and bridge exceptions. Blender explicitly warns against persistent Python threads and recommends separate processes for independent work. See [threading guidance](https://docs.blender.org/api/main/info_gotchas_threading.html).

Prefer direct data APIs for structured helpers where possible. Blender operators can depend on selection, mode, active object and editor context. Inspect `poll()` and operator return status; absence of an exception alone does not prove success. Return the active context with context errors so the agent can correct them. See [operator limitations](https://docs.blender.org/api/main/info_gotchas_operators.html) and [operator result semantics](https://docs.blender.org/api/current/bpy.ops.html?highlight=bpy+ops+view_3d).

Do not promise transaction rollback for arbitrary Python. A script can partially mutate a scene before raising an exception, invoke external programs or terminate Blender. Record the operation before execution and return `partial_change_possible` on failure. Recovery should reopen a saved checkpoint in a fresh process when necessary. A lost response must produce an unknown-outcome state until the controller reconciles the operation ID; automatically replaying a mutation can duplicate objects or imports. These are proposed protocol requirements.

### Official Modal Blender example

Modal publishes a [parallel Blender rendering example](https://modal.com/docs/examples/blender_video). It installs `bpy==4.5.0` with Python 3.11 and X11-related dependencies, opens a `.blend`, configures rendering and distributes frames across Functions, including an L40S configuration. This is concrete evidence that Blender rendering on Modal is an intended use case. Its reported timings describe one example, not a performance estimate for this project. It does not validate a persistent Sandbox bridge or a remote desktop.

Evaluate two images: a pinned Blender executable for the interactive session and an optional matching `bpy` package for batch workers. Version and add-on parity matter more than using the same entry point. Sandboxes remain the proposed interactive substrate; Modal Functions are an additional option for bounded render jobs. Avoid copying a large scene as bytes for every frame. Workers should reference a saved revision and shared assets, then write independent outputs. This adaptation is a design proposal.

### Visual control and maximum capability

Treat visual operations as distinct capabilities:

- Camera renders: image generation from scene cameras with reported engine and settings.
- Diagnostic views: temporary inspection cameras or a separate scene copy; report any changes to the live scene.
- Viewport captures: the actual editor state, selection, overlays and shading mode; requires a tested graphics/editor environment.
- Human remote interaction: a desktop/video transport with input control, authentication and bandwidth requirements beyond MCP tool calls.

Blender's GPU documentation says offscreen objects depend on their creating graphics context. A CUDA device and Xvfb alone are not proof of an accelerated viewport. Test Cycles GPU rendering, Eevee rendering and editor capture separately. The retrieved [GPU documentation](https://docs.blender.org/UATEST/api/current/gpu.html) is a UATEST mirror, so verify the selected release's documentation and runtime before fixing graphics dependencies.

For broad coverage, add runtime API discovery: Blender version, available operators, RNA properties, installed add-ons, import/export operators, engines and devices. Return filtered, paginated results instead of flooding the harness context. Keep `execute_python` available for specialized operations. Add structured helpers where they improve reliability, especially inspection, saving, visual feedback and dependency reporting. This avoids making a large fixed tool menu the limit of Blender access.

### Project portability and render correctness

Packing resources cannot replace dependency inventory. Blender's [packed-data manual](https://docs.blender.org/UATEST/manual/en/dev/files/blend/packed_data.html) states that some external files, including video media, cannot be packed. This retrieved page is a development mirror; the exact supported types must be checked against the pinned Blender release. Inventory linked libraries, image sequences, UDIMs, VDBs, simulation caches, fonts and media, and report unresolved dependencies before dispatch.

A render revision should include the `.blend`, dependency hashes, Blender build, add-on set, engine/backend, color configuration and job settings. Simulation jobs may depend on earlier frames or baked caches; do not assume arbitrary frame parallelism. Preserve seeds and effective settings, but do not promise byte-identical GPU output across hardware. These are proposed reproducibility requirements.

### Codex and MCP integration details

The [Codex MCP documentation](https://developers.openai.com/codex/mcp) documents a default startup timeout of 10 seconds and tool timeout of 60 seconds. Start the local MCP server promptly and create remote sessions lazily through tools. Return a job ID for long work, then provide status, cancellation and artifact retrieval. Do not keep a render inside one tool call simply by increasing its timeout.

Use standard tool results for the essential workflow. Return a small structured summary with IDs, revision, warnings and timings, plus inline image content for visual inspection and a full-resolution artifact reference. A remote filesystem path alone is not an image the model can inspect. Provide retrieval through tools even if resources are also registered. Reserve stdout for MCP transport and send diagnostics to stderr. Verify all of this in the actual harness.

The official Python SDK now documents `MCPServer` and Python 3.11+ on its main branch; older integrations can use `FastMCP`. Pin a released version and use its matching documentation instead of combining examples from different major versions. See the [SDK repository](https://github.com/modelcontextprotocol/python-sdk). The research does not establish a final dependency pin.

### Existing tools: verified scope and remaining gaps

The original community repository now identifies its package as `mcp-for-blender`, formerly `blender-mcp`; record that naming change when evaluating dependencies. Its README describes Codex integration as well as Blender control. See the [maintainer repository](https://github.com/ahujasid/blender-mcp). The [blendmcp README](https://github.com/owenpkent/blendmcp) identifies it as a fork and documents scene inspection, Python execution, screenshots and asset integrations. Its telemetry statement is a maintainer claim, not an independent audit.

The [Blender Bridge repository](https://github.com/Blender-Bridge/blender-bridge) describes a Windows application with a subscription and identifies the repository as documentation and releases. Treat it as a product reference, not a selected code dependency.

Blender Lab's [tagged source page](https://projects.blender.org/lab/blender_mcp/src/tag/v0.3.0) is indexed with a stdio server, TCP add-on bridge, Python execution and screenshot tools. The indexed README warns that generated code runs without data-protection guards. Direct retrieval of both the source page and Blender Lab landing page returned HTTP 403 during this audit. Official provenance is supported by the Blender-owned site and repository; exact license, authentication, current tool inventory, supported versions and headless behavior remain unresolved. Do not choose or deploy this bridge until its tagged source can be inspected.

### Next evidence to collect

The next step is a small feasibility implementation with recorded results, not another documentation-only acceptance claim. It should prove a persistent scene across commands, a context failure with useful diagnostics, a real GPU render, inline image return through Codex, verified project download, and recovery after forced Blender exit. Measure cold creation, warm inspection, edit-to-image time, transfer bytes, local memory and remote resource use. Record median and tail latency for repeated calls; set interaction targets from those measurements.

Then test the harder boundaries: connection loss after mutation, failed checkpoint commit, a missing linked asset, a render worker using an old revision, client restart, idle termination, and GUI capture if required. Record each as passed, failed or not run. No runtime tests, cloud launches, cost benchmarks or compatibility tests were performed during this research audit.

## Implementation status

The subsequent prototype has now completed live CPU and GPU checks. See [prototype validation](prototype-validation.md) for the tested scope, corrections and remaining limits. The research audit above records the evidence available before those execution tests.
