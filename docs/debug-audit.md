# MCP debug audit and command coverage

Audit date: 2026-11-12 (local time). This pass checked runtime behavior, not only tool discovery. The implementation exposes 26 MCP tools and retains full Blender Python access. It is a trusted local MCP control plane for remote Blender on Modal.

## Defects corrected

| Defect | Correction | Evidence |
| --- | --- | --- |
| One stalled project blocked all local session creation | Separate controller lock per project | Concurrent-lock regression test; independent CPU/GUI/GPU provisioning |
| GPU provisioning or an execution-control request could wait indefinitely | Bounded async requests; provisioning cancellation checks the sandbox's ownership tag | Local timeout/ownership tests and live successful provisioning; the original stalled allocation was manually cancelled |
| Supervisor cancellation could act on stale state as Blender completed a job | Shared lock around claim, completion and cancellation transitions | Concurrency and stale-completion tests; live cancellation/deadline checks |
| New edits could race with shutdown's save | A stop barrier drains accepted jobs, rejects later jobs and reuses its final save | Live pending-stop test with rejected late mutation and retry |
| Queued jobs could silently run against rolled-back state | Fail pending jobs after a worker restart | Live timeout with a queued mutation that must never execute |
| A cancelled or failed stop could leave submission blocked | Reopen submission on failed or cancelled stop jobs | Regression tests |
| Saved state was trusted without verification | Check checkpoint hashes at startup; store per-revision manifests | Live checksum-failure test |
| A bad latest record left no startup recovery route | Explicit `restore_revision` at creation; checkpoint listing and diagnostics after termination | Live recovery of an intact marker from a selected revision |
| Large Python requests were passed as command-line arguments | stdin-based JSON requests | Live request exceeding 200 KB |
| Temporary render settings could leak into the live session | Restore frame, engine, resolution, samples, device backend and device flags | Live frame-restoration check; CPU, L4 and L40S rendering regressions |
| Agent inspection required ad-hoc scripts and guesses | Structured environment, object, nodes, dependencies and RNA discovery tools | Native workflow checks |
| Dynamic enum discovery could report an empty list for valid choices | Resolve inherited Python registrations and callbacks; label incomplete results | glTF GLB/GLTF_SEPARATE live check and mixin regression |
| File listing traversed internal state and exposed symbolic-link entries | Pruned traversal with pagination and explicit relative paths | Internal-state/symlink regression |
| Native errors were hard to diagnose and ended status was generic | Log tail tool, persisted startup diagnostics, durable ended-job status | Native log/error checks and checksum-failure diagnostics |
| Oversized structured results could overwhelm a harness | 1 MB result limit; artifacts for larger outputs; concise recent-job summaries | Runtime guard and bounded inspectors |

## Verified command coverage

| Area | Available control and inspection | Observed checks |
| --- | --- | --- |
| Scene and geometry | Python, installed operators, scene/object inspection | Mesh creation, transforms and subdivision |
| Materials and lighting | Python, shader-node inspection, camera render | Assigned node material, shader links, teal material preview |
| Geometry Nodes | Python and group/socket/link inspection | Pass-through graph and modifier reference |
| Animation | Python, operators and object action inspection | Location keyframes at frames 1 and 10; timeline control |
| Rigging | Python, operators and armature inspection | Two-bone armature and parent relationships |
| Import/export | Discover installed operators and run structured arguments | GLB export, file verification and reimport |
| GUI viewport | Selection, framing, axis view, orbit, pan, zoom, shading, overlays and screenshots | SOLID, WIREFRAME and MATERIAL; recovery screenshot |
| Rendering | Cycles jobs, inline PNG and verified downloads | CPU, NVIDIA L4 CUDA and NVIDIA L40S CUDA |
| Project files | Upload, download, save, open, checkpoint list and restore | Packed-image export/reopen and cross-session recovery |
| Diagnostics and lifecycle | Runtime/log inspection, cancellation, deadlines, stop and ended status | Queued cancellation, stale-job rejection, pending stop, idle save and emergency stop |
| Custom operations/add-ons/simulation | Full Python and installed operator access | Available as a control mechanism; individual add-ons and simulation engines are not exhaustively tested |

The native GUI workflow completed 32 checks through 54 MCP calls, including three expected failed jobs. It used 20 unique native tools and confirmed all 26 tools were exposed. The other transport and lifecycle checks exercised rendering, upload/open, cancellation and session listing. These results do not imply that every Blender feature, property or operator was independently tested.

## Evidence and repeatability

The compact [debug validation record](debug-validation-results.json) records automated, native, lifecycle, recovery and GPU results. Full native responses, logs and exported files remain in the ignored `.validation/` directory. The broad native run preceded the dynamic-enum callback correction; that correction was separately checked live and in a regression test. The final native check passed 10 additional assertions for current API discovery, archived checkpoints and startup recovery. A separate live check then confirmed that dynamic enum defaults are reported as unavailable without producing RNA warnings. Sixteen automated tests passed, including timeout ownership, per-project locking, queue races and inherited enum metadata. All validation compute was confirmed stopped.

The core checks are reproducible with:

```sh
uv run pytest -q
uv run ruff check .
uv run python scripts/validate_live.py
uv run python scripts/validate_lifecycle.py
uv run python scripts/validate_recovery.py
```

Live commands provision billable Modal resources. GPU runs can select `RBM_TEST_GPU=L4` or `L40S`. Set `RBM_TEST_PROJECT` and `RBM_TEST_REPORT` to unique values when running acceptance scripts concurrently. Native Codex validation requires a fresh trusted project session so the expanded MCP schemas load.

## Remaining boundaries

- Blender is pinned to 4.5.0. Installed render engines and add-ons define the available operations.
- GUI drawing uses remote Mesa llvmpipe. Hardware-accelerated viewport drawing is not established by the CUDA camera-render tests.
- There is no desktop video stream, raw input forwarding, automatic asset synchronization or independent render-worker pool.
- Dependency inspection explicitly reports incomplete coverage. Linked assets, simulation caches, media sequences and add-on-specific data still require deliberate handling.
- A lost control response can leave a mutation's outcome unknown. Inspect its recorded state before resubmitting it.
- Raw Python has the authority of the remote process. Resource and network limits are infrastructure controls, not a Python permission system.

Dynamic enum callbacks follow Blender's [EnumProperty callback contract](https://docs.blender.org/api/4.5/bpy.props.html). The request stream uses Modal's documented [stdin APIs](https://modal.com/docs/guide/sandbox-spawn).

## Independent-thread validation follow-up

Subsequent independent validation used a new top-level project thread for each testing round, with standalone briefs, no access to previous testing-thread conclusions, and no source-edit authority. The broad independent run passed its core workflows but found that auto-framing could clip animated geometry. Its evidence remains in `.validation/independent-native-report.json`; the finding is not relabeled as a clean pass.

The first correction applies the requested frame and updates the view layer before selection framing. A separate new regression thread confirmed correct animation-time targeting but measured a remaining single-cube clipping error: one projected corner was outside the actual viewport. That failed evidence remains in `.validation/animated-framing-regression.json`.

The final correction disables smooth view transitions during synchronous commands, recomputes evaluated world-space bounds, and verifies their projection into the actual viewport region. If required, it increases view distance until the projected bounds fit with a margin. Caller-supplied target or distance still takes precedence.

A third, entirely new test thread validated the final fit behavior in four cases: frame 1, a combined jump to frame 24, an identical repeat, and a mesh-plus-armature selection after a frame jump. Independent projection code confirmed that all eight cube corners were inside the actual 1056 × 691 region in every case. The repeat had zero target/distance drift. The tester also inspected inline screenshots and confirmed session termination. Evidence is in `.validation/viewport-fit-final.json`; earlier failed evidence remains intact. Runtime hash: `688856590556709bb8e91a773db6f0f601dcf2a2c082d855214983d91756b0d9`. All test compute was confirmed stopped.
