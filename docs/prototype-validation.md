# Prototype validation

Date: 2026-11-12. These checks used real Modal Sandboxes and the official MCP Python client over stdio. No local Blender process performed the remote edits or renders. A local Blender process was used only to reopen an exported project for verification.

## Observed results

| Check | Result |
| --- | --- |
| MCP initialization, discovery and invalid-input handling | Passed in the automated stdio test. |
| Persistent remote scene edits | Passed on Blender 4.5.0. Objects persisted across calls. |
| CPU camera render | Passed using Cycles. |
| GPU camera render | Passed using CUDA on an NVIDIA L4. The worker reported the selected device. |
| Inline image response | Passed through MCP image content. The rendered image was also inspected visually. |
| Upload and download | Passed with matching SHA-256 hashes. |
| Saved project and dependency readback | Passed after exporting, uploading and reopening a `.blend`; the sphere and one packed image remained present. |
| Local exported-project readback | Passed in Blender 5.2.3 LTS; the sphere and packed image were present. |
| Python exception | Returned a traceback and the partial-change warning. |
| Running-job cancellation | Stopped Blender and restored the prior checkpoint. An unsaved change to the sphere was discarded. |
| Explicit checkpoint restore | Passed. |
| Job deadline | Returned `timed_out`; a subsequent scene inspection succeeded after recovery. |
| Cross-session project recovery | Passed when a later sandbox loaded the previous session's saved project. |
| Compute cleanup | All validation sessions stopped. A final Modal query returned no active sandboxes in the prototype App. |
| Local checks | Five tests passed; Ruff checks and formatting passed; package build passed. |

The final CPU run included the current Blender worker and export implementation. The L4 run preceded the later save-copy export correction and checkpoint-listing addition; GPU rendering and cancellation passed on that earlier worker revision. The local transfer publication change was then covered by a test that checks checksum failure and overwrite prevention.

## Corrections made during validation

- Blocking Modal SDK calls initially ran on the MCP event loop. Tool handlers now run those calls in worker threads in the local server. Blender itself uses no persistent Python background threads.
- An early session contained an older file-transfer protocol without remote checksums. The acceptance run rejected that session's download response. A new session used the updated image and passed both transfer checks.
- Exporting a checkpoint by copying its bytes can preserve relative paths for the wrong directory. Export now uses Blender's save-copy operation with path remapping.
- The first packed-image fixture had no Blender user, so Blender removed it on save. The fixture now retains the image, and acceptance requires a packed image after reopening the exported file. Ordinary project assets still require explicit dependency management.

## Evidence and limits

The compact [validation record](validation-results.json) records the successful CPU and GPU checks. Full logs, PNGs, exported `.blend` files and raw reports remain in the ignored `.validation/` directory. Run `scripts/validate_live.py` to repeat the billable acceptance sequence.

These are small-scene functional checks. They do not establish production reliability, tail latency, total cloud cost or large-project performance. At this stage only the official Python MCP client had been tested. The subsequent Codex and GUI validation is recorded below. Claude Code and OpenCode validation was removed from scope at the user's request. GPU capacity, preemption, hard lifetime expiry, storage exhaustion and automatic idle shutdown were not fault-tested. At this stage no GUI viewport was implemented. GUI support has since been added as recorded below; remote desktop streaming/input, independent render workers and automatic asset synchronization remain unimplemented.

Arbitrary Python can spawn processes and change mounted project files. Running-job cancellation terminates the Blender process; independently spawned child processes may require stopping the entire session. Successful tool completion does not certify arbitrary add-on behavior or complete project portability.

## Codex and live GUI validation

Date: 2026-11-12. Scope was narrowed to Codex by the user. The project now includes `.codex/config.toml` and the repeatable `scripts/setup_codex.py` installer. Home-level MCP configuration and plugins were excluded from the test. No new credentials were required.

The actual **Codex CLI 0.146.0** completed seven MCP calls against a real Modal GUI session: scene inspection, Python execution, job status, two viewport-control operations and two viewport screenshots. Both screenshots arrived as MCP image blocks. Codex reported the difference between a solid front-orthographic square and an isometric wireframe cube. Its final result reported success. See [Codex evidence](codex-validation-results.json).

A second MCP test confirmed selection, framing, axis changes, shading, timeline control, orbit/pan/zoom, nonempty screenshots and different image hashes after changing the view. It then forced a job deadline, verified the restored GUI could still respond and capture an image, opened an exported `.blend`, and verified that the persistent timer still accepted viewport commands. See [GUI evidence](viewport-validation-results.json). The current headless workflow also passed its regression sequence after the worker-loop refactor.

The remote GUI uses the official Blender 4.5.0 Linux executable on Xvfb at 1280 × 800. The retrieved archive matched SHA-256 `1188b95cc12321c770b631939f7c25a096910b6f884a990bf9c0f62d52b38aec`. The graphics renderer reported Mesa llvmpipe, OpenGL 4.5. This validates remote software viewport drawing, not hardware-accelerated viewport drawing. The earlier NVIDIA L4 Cycles rendering result remains a separate test.

Blender's `screen.screenshot` operator returned black images under this virtual display. The implementation now captures the real X11 framebuffer through Xlib and encodes it as PNG. Initial first-run setup also obscured the viewport; the GUI image now creates preferences during its build and disables the splash screen. Both corrections were verified visually.

The isolated Codex test exposed two setup requirements. Project trust must be available when Codex loads configuration, and custom Modal/session environment variables must be listed in `env_vars` for forwarding to the MCP process. The installer includes those variables and approves this project's MCP tools; the validator uses an isolated trust registry and a temporary reference to the existing Codex authentication file.

GUI input is exposed through Blender API operations and structured viewport tools. There is no continuous desktop video stream or raw mouse/keyboard forwarding. SOLID and WIREFRAME drawing were tested; MATERIAL and RENDERED shading are exposed but were not separately validated. No claim is made for GPU-accelerated Eevee viewport behavior or arbitrary editor layouts.

Local validation now includes seven automated tests, Ruff checks, formatting and package build. The capture, viewport, worker and supervisor modules are included in the wheel. All test compute was stopped after validation; project volumes and output artifacts remain available.

## Debug audit follow-up

A subsequent debug pass added lifecycle race fixes, startup recovery and seven inspection/operator/diagnostic tools. See [debug audit](debug-audit.md) and its evidence record for the current 26-tool surface, native feature checks, CPU/GPU regressions and failure-path results. Earlier limitations above describe the earlier implementation stage.
