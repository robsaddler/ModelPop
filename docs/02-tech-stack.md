# ModelPop — technology stack

**Python-first, single process** (ADR-0007). Everything open source; the only spend is AI credits.
Versions verified on this machine, 2026-09-23.

## Platform

| Concern | Choice | Notes |
|---|---|---|
| Language | **Python 3.12** for the app | 3.14 works for CAD, but PyTorch needs 3.12 |
| Second env | Python 3.12 venv for AI generation | PyTorch/CUDA stack, kept separate |
| Target | Windows 11 x64 | i9-14900HX, 128 GB RAM, RTX 4090 Laptop 16 GB |

> **C++ speed with no C++ toolchain.** OCCT, VTK, NumPy and Open3D ship as precompiled binary wheels.
> `build123d` + OCCT 8.0.1 installed on this machine's Python in one command with no compiler present.

## CAD kernel — verified in spike S7

| Role | Choice | Licence |
|---|---|---|
| Kernel | **build123d 0.13.0** over **cadquery-ocp 8.0.1** (OCCT 8.0.1) | Apache-2.0 |
| Sketching, extrude/revolve/sweep/loft, booleans, fillet, chamfer | build123d, in-process | |
| STEP / STL / **3MF** export, STEP import | build123d native | |

Measured: fillet all 12 edges of a cube in **7 ms**; boolean subtract exact to the last digit; native
3MF export. Every operation that CADability could not do (spike S1) works here.

## Viewport and UI

| Role | Choice | Licence |
|---|---|---|
| Viewport | **PyVista 0.49 / VTK 9.7** | BSD |
| Desktop shell | **PySide6** (Qt 6) | LGPL — fine, not sold |
| Embedding | `pyvistaqt` | BSD/MIT |

Measured: 983,040 triangles at 28–36 FPS, ray picking at **0.0045 ms** with a cached `vtkCellLocator`,
clipping planes working. **Open item:** VTK used the Intel iGPU in the spike, not the RTX 4090 — force
the discrete GPU and re-measure in Phase 1.

## Mesh processing

| Role | Choice | Licence |
|---|---|---|
| General mesh ops, IO | **trimesh** | MIT |
| Robust booleans, manifold gate | **manifold3d** | Apache-2.0 |
| Repair, watertight | **pymeshfix** | **AGPL — now allowed** |
| Filters, remesh, decimate | **PyMeshLab** | **GPL — now allowed** |
| Point clouds, registration, voxel | **Open3D** | MIT |
| Voxel remesh, boolean, decimate (fallback) | **bpy** (Blender 5.2 installed) | **GPL — now allowed** |

**The copyleft unlock.** Because the app is open source and not sold, GPL and AGPL dependencies are
available. `pymeshfix` in particular is the best automatic watertight repair there is.

## AI, vision, reconstruction

| Role | Choice |
|---|---|
| Frontier model | Anthropic SDK (`anthropic`), BYO key |
| Alternatives | OpenAI-compatible, **Ollama** (installed) |
| Mesh generation | **TRELLIS.2** primary, **TripoSG** fallback — both MIT (ADR-0004) |
| Part-aware generation | PartCrafter (MIT) |
| Segmentation | SAM 3 |
| Reconstruction | **COLMAP 4.2.0** (new BSD) + **OpenMVS 2.4.0** (AGPL-3.0) via `subprocess` — both installed and verified |
| Scale from marker | **OpenCV** ArUco/ChArUco |

**Neither reconstruction tool is in winget.** Both ship official Windows binaries on their own
GitHub releases and are installed by unzipping, which is why they sit in `C:\Tools` rather than
under `Program Files`. AGPL on OpenMVS is fine here on both counts: this application is open
source, and it drives the binary as a separate process rather than linking it.

**Still excluded:** the **Hunyuan3D** family. Its licence excludes the UK *territorially*, which the
open-source relaxation does not affect.

## Printing

| Concern | Choice |
|---|---|
| Slicing | **Bambu Studio 2.8 CLI** via `subprocess` — verified working (ADR-0006) |
| 3MF | build123d native for plain 3MF; hand-written zip only for Bambu's project sidecars |
| Printer comms | Bambu LAN mode (MQTT + FTPS), dry-run by default |

`subprocess.run([...])` with a list avoids the argument-quoting trap that bit the C# version.

## Engineering tooling

| Concern | Choice |
|---|---|
| Tests | **pytest** |
| Property-based | **hypothesis** — the highest-value technique for geometry |
| Snapshots | **syrupy** |
| Coverage | pytest-cov |
| Mutation | mutmut |
| **Layering rules** | **import-linter** — enforces the architecture, as ArchUnitNET would have |
| Types | **mypy** (strict) |
| Lint / format | **ruff** |
| Packaging | **uv** for environments and locking |

## Open questions

- VTK on the discrete GPU (Phase 1).
- TRELLIS.2 peak VRAM at 1024³ on 16 GB is a community claim — measure it.
- Filament binding in the slicer CLI returned zero grams; fix in Phase 2.
- Texture→displacement detail rescue is unsolved anywhere; Phase 8 is research.

## Traps already paid for

1. **Windows path length breaks pip.** Keep the project and venv at a short path.
2. `vtkOBBTree.IntersectWithLine(..., None)` **segfaults** — use `vtkCellLocator` with the full
   argument list.
3. `Plotter.render()` off-screen does not block; frame timing there is meaningless.
