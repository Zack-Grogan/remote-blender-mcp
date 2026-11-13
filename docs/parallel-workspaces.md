# Independent Blender workspaces

## Boundary

Codex owns agent spawning, item assignment, scheduling, retries, progress and aggregation. The MCP provides resource and Blender operations. It does not contain a task broker, batch scheduler, worker assignment service, generated delegation brief or model launcher.

The server still exposes 26 tools. `create_session` adds two resource parameters:

- `new_workspace=true`: create a fresh workspace regardless of other sessions with the same project label.
- `workspace_id`: open or recover a particular independent workspace. It may also be a caller-chosen UUID for stable resource creation across retries.

Do not combine those options. The default behavior remains a persistent workspace addressed by project name. A fresh workspace gets a separate Sandbox, Blender process, Volume, scene, job queue, checkpoints and artifact paths. Its physical resource namespace contains a UUID and is distinct from valid project slugs.

A repeated `new_workspace=true` call intentionally creates another workspace. If a request's outcome is unknown, the caller must reconcile it before retrying. A caller that needs an idempotent identity can supply a stable `workspace_id` instead.

## Codex workflow

The project [AGENTS.md](../AGENTS.md) tells Codex to create one fresh subagent per independent requested item, within the harness's capacity. Either the lead supplies a fresh session or the worker requests one. Each worker uses only its own session, awaits its jobs, inspects an actual render, saves/downloads outputs to a distinct local directory and stops only its own compute.

Remote filenames may be identical across workspaces. Codex chooses distinct local output directories and combines the returned files; the MCP does not define an output layout. The download tool refuses overwrites. For multiple views of one asset, Codex must stage a consistent source project into each workspace.

This separates resources for trusted agents. It is not authentication or hostile-tenant isolation between callers that share local filesystem and Modal credentials. The harness also owns resource budgets. Volumes remain after compute stops.

## Validation — 2026-11-13

The live resource test used the actual stdio MCP server and two concurrent `create_session` calls with the same project label. Both had different workspace IDs, sandbox IDs and Volumes. Independent jobs overlapped in time. Both wrote `/workspace/same-name.txt` with different contents, which downloaded intact to separate local directories. A foreign job ID was rejected by the sibling workspace.

Codex then spawned two fresh render subagents. Each used native Blender MCP tools against its own assigned session. One rendered a red beveled cube; the other rendered a blue sphere. Both intentionally used the object name `SharedObject`, material name `SharedMaterial` and remote output names `output/render.png` and `output/scene.blend`. Each inspected the inline render, verified its own marker, downloaded both files and confirmed its own session ended.

The lead verified each file's SHA-256 against the worker's report. Reopening each workspace by ID preserved its own scene marker and Volume. Stopping one reopened workspace left the sibling usable. All validation compute was confirmed stopped.

[Compact validation record](parallel-workspaces-validation.json) contains the resource checks and artifact hashes. Raw reports and files are in `.validation/parallel-workspaces/`. No speedup factor is claimed: overlapping resource jobs and independent agent work were demonstrated, not a production benchmark.

Twenty automated tests passed. They include resource namespace separation, default-workspace compatibility, workspace recovery, concurrent startup with a blocked sibling, invalid option rejection, and the existing Blender/runtime regressions. Tool-discovery tests confirm that no task/batch orchestration tools are exposed.
