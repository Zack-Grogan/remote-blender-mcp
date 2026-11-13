# Writing preference

Use the `asd-ste100` skill by default when writing or revising technical instructions and documentation, including docs. Use its STE-flavored mode for explanatory prose. Preserve facts, requirement strength (must, should, may), conditions, exact identifiers, and historical quotations. Respect the requested format and higher-priority instructions. Do not claim certified ASD-STE100 compliance based on this skill or its structural linter.

# CPU/GPU decision policy

Codex owns this decision. Use CPU by default. Unless the user explicitly requires a GPU, allocate one only when the plan includes heavy animation rendering or expensive final renders and the expected benefit justifies the cost. Do not implement these decisions as server heuristics, automatic upgrades or restrictions in the MCP.

| Planned work | Default | GPU decision |
| --- | --- | --- |
| Modeling, mesh editing, UVs, retopology, scene layout, import/export | CPU | These tasks alone do not justify a GPU allocation. |
| Rigging, weights, constraints, keyframe editing, animation blocking | CPU | An animation project does not imply GPU use during editing or setup. |
| Materials/nodes, routine viewport inspection, diagnostic images and small test renders | CPU | Keep routine feedback and validation on CPU. The current GUI uses remote software OpenGL; reserving a GPU alone does not accelerate it. |
| Simulation or baking | CPU | Long runtime alone is insufficient. Check the chosen operation's actual acceleration support; do not assume a GPU helps. |
| Heavy multi-frame final animation rendering | Consider GPU for the render stage | Check the render engine/backend, representative frame cost, scene memory, deadline and total expected cost. Use GPU only if the plan supports the benefit. |
| High-resolution or high-sample final stills, complex lighting/volumes, large final render batches | Consider GPU for the render stage | Compare a representative CPU render or credible estimate with the GPU's expected time and cost. A small preview or ordinary still stays on CPU. |

Before choosing a GPU, state a brief reason in the plan: which heavy render work needs it, the supported backend, the proposed resource profile, expected usage window and the stop condition. Use current pricing and representative measurements when estimating cost, and label estimates. Keep CPU when the benefit is uncertain. Do not silently give every parallel worker a GPU; decide per item and stage and include that choice in each worker's handoff.

Keep modeling and preparation on CPU when practical. Save the project before moving to a justified GPU render stage. If a GPU is allocated for Cycles, select and verify its CUDA/OptiX render device; reserving the GPU does not automatically move Blender operations onto it. Stop the GPU session promptly when the render stage ends; do not keep it reserved for ordinary editing, idle planning or unrelated tasks. Workspace persistence can preserve the scene across a resource-profile change after compute stops.

# Parallel Blender work

Codex owns agent orchestration. The MCP provides Blender resources and operations. Do not add agent spawning, task scheduling, worker assignments, retries or aggregation policy to the MCP server.

When the user requests multiple independent 3D items or renders, Codex should use one fresh subagent per item, bounded by the harness's available capacity. Start remaining items in waves when needed. A single assigned item does not authorize its worker to spawn more agents.

Each render worker must:

1. Call `create_session` with `new_workspace=true`, or use a fresh workspace/session supplied by the lead. The shared `project` value is a label; the returned `workspace_id` identifies its separate persistent data. Never use the shared default session for independent items.
2. Use only its own `session_id`. Never modify, cancel or stop a sibling's session. Each workspace has a separate Blender process, Sandbox, Volume, scene, job queue and checkpoints.
3. Await terminal jobs and inspect an actual image. Save an editable `.blend` when appropriate. Identical remote filenames are safe across independent workspaces.
4. Download outputs to its own local directory, using the workspace or session ID in the path. Codex chooses these paths and aggregates the results; the server does not define an output layout.
5. Stop only its own compute when finished. Preserve saved data. Report failures and pending cleanup explicitly. Emergency discard loses that workspace's unsaved changes.

The lead owns delegation, concurrency limits, retries, progress reporting, cancellation policy and final aggregation. For multiple views of one asset, prepare a consistent source project and stage its files into each workspace before rendering.

# Validation ownership

Use a fresh agent context for each independent validation round. Do not reuse a previous testing/review conversation as independent evidence. Create top-level threads only when the user explicitly requests them. Validators may write their own evidence and artifacts, but must not edit the implementation they validate. The lead fixes findings and requests a fresh validation round when needed.
