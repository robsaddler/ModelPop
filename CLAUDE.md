# ModelPop — working agreement

Turn an idea, a photo, or a prompt into a printable, editable 3D model for a Bambu Lab P2S.
Read `docs/00-plan.md` first. Architecture: `docs/01-architecture.md`. Standards:
`docs/04-engineering-standards.md`.

## Hard constraints

1. **Open source only.** The only money spent is AI API credits. **GPL and AGPL are fine** — the app is
   open source and not sold. Record every dependency's licence in the ADR.
2. **CAD tools live in the app.** Never propose shelling out to Fusion or FreeCAD for *editing*.
   Driving the Bambu Studio CLI for *slicing* is the one sanctioned external process (ADR-0006).
3. **Bring-your-own AI keys**, set in an in-app settings panel, stored in the OS credential store.
   Never hard-code a key, never log one, never commit one.
4. **SOLID, patterns, modularity and tests are the point**, not decoration.
5. **Outcomes first.** When a choice is between elegance and a working printed object, pick the object.

## The stack (ADR-0007 — Python-first, single process)

- **Python 3.14** for the app (`requires-python >= 3.13`, and CI tests both). A **separate** venv
  holds the PyTorch/CUDA generation stack, pinned lower because Torch has no 3.14 wheels.
- **CAD: build123d + cadquery-ocp (OCCT 8.0.1)**, in-process. Fillets, chamfers, booleans, STEP/STL/3MF.
- **Viewport: PyVista / VTK**, shell in **PySide6**.
- **Mesh: trimesh, manifold3d, pymeshfix (AGPL), PyMeshLab (GPL), Open3D, bpy (GPL)**.
- **Slicing: Bambu Studio CLI** via `subprocess`, out of process.
- **Generation: `trellis.cpp`** (MIT), a native binary driven as a subprocess, running TRELLIS.2-4B
  weights. **Not** the Python TRELLIS.2 — it needs 24 GB against this card's 16 and does not build
  on Windows (ADR-0010). **Hunyuan3D stays excluded** — its licence bars the UK territorially, which
  the open-source relaxation does not change (ADR-0004).

This project was originally C#/.NET. ADR-0007 supersedes ADR-0002 and ADR-0003 with measured evidence.
Do not reintroduce C# without reading ADR-0007 and spikes S1, S3 and S7.

## Non-negotiable invariants

- **Nothing mutates a model except through the command bus** (ADR-0001). An LLM emits validated, typed
  commands, never executable code that touches the process.
- The **domain** package depends on nothing — no CAD kernel, no VTK, no HTTP client.
- The **application** package imports no adapter module and no third-party geometry type.
- **View-models import no UI framework.** Views bind to them; headless tests drive them.
- Model output is untrusted data. Validate, clamp and range-check before use.
- Layering is enforced by **import-linter** in CI, not by goodwill.

## Definition of done

Tests that would fail without the change; `ruff` and `mypy --strict` clean; `import-linter` passes;
≥ 80% coverage on new code; docstrings on public API; an ADR if an architectural choice was made.

## Test discipline

- Fast suite **under 10 seconds**. Slow tests marked `@pytest.mark.integration`.
- Geometry gets **hypothesis** property tests: the boolean of two watertight solids is watertight;
  `undo(apply(cmd))` restores the document hash; scale by `s` then `1/s` is identity within tolerance.
- Snapshots (syrupy) use **fixed rounding**, 6 dp. Never accept a snapshot you have not read.
- AI adapters run against recorded fixtures, including malformed and adversarial responses.
  Scored evals are opt-in and never block the build.

## Verified local facts (measured, not assumed)

- **Bambu Studio 02.08.02.61** at `C:\Program Files\Bambu Studio\bambu-studio.exe`. CLI slicing
  **works** — see `docs/research/spike-bambu-cli.md`.
- The CLI writes **no stdout and no usable exit code**. Status is `result.json`, written to
  **`--outputdir`** (the working directory is only a fallback). Pass arguments as a **list** to
  `subprocess`, never a joined string.
- P2S build volume **256 × 256 × 256 mm**; default process `0.20mm Standard @BBL P2S`; support
  defaults `support_type: tree(auto)`, `support_threshold_angle: 30`, `enable_support: 0`.
- **build123d fillets all 12 edges of a cube in 7 ms**; booleans exact (spike S7).
- **VTK picking: 0.0045 ms** with a cached `vtkCellLocator`; 983k triangles at 28–36 FPS —
  but that was on the **Intel iGPU**, not the RTX 4090. Force the discrete GPU and re-measure.
- RTX 4090 Laptop has **16 GB VRAM**. One large generative model at a time.

## Traps already paid for — do not rediscover these

1. **Windows path length breaks `pip`.** Keep the project and its venv at a short path.
2. `vtkOBBTree.IntersectWithLine(p1, p2, points, None)` **segfaults.** Use `vtkCellLocator` with the
   full argument list — and it is far faster anyway.
3. `pyvista.Plotter.render()` off-screen does not block, so frame timing there is meaningless
   (it reported 166,021 FPS). Measure in a real window with `update()`.
4. Python 3.14 is on this machine but **not on PATH**; use the `py` launcher. It works for the CAD
   stack (cp314 wheels exist) but **not** for PyTorch.
5. **`os.kill(pid, 0)` KILLS the process on Windows.** The portable POSIX "does this exist" idiom
   maps onto `TerminateProcess` for any signal but the two console events. Measured: a sleeping
   child went from running to exit code 3221225794 on being probed. Use `OpenProcess` +
   `WaitForSingleObject(handle, 0)` instead — see `modelpop.generation.gpu_lease`.
6. **A `QThread` worker with no Python reference is garbage collected**, the queued `started`
   connection dies with it, and the thread runs an empty event loop forever. No exception, no
   output, no log line — the button simply does nothing. Keep the worker alive, not just the thread.
7. **View-models announce from whichever thread did the work.** Touching a widget from a worker
   thread is undefined; in practice the interface silently stops updating. Marshal back with a
   signal.
8. **VTK does not fail on a GPU-less runner, it takes the process down** with an access violation.
   Hence the `renders` marker, deselected in CI.
9. **Off-screen `Plotter.screenshot()` hands back the previous buffer** after a change that does not
   dirty the scene graph - a clipping plane, for one. The picture never appears to move and the
   feature looks broken when it is not. Call `render()` first, every time.
10. **Measure a rendered change between two screenshots, not against the background.** The viewport
   background is a gradient, so "differs from the top-left pixel" counts most of the sky as drawn
   and swamps the model. That reading cost an hour and nearly bought a mapper swap that fixed
   nothing - PyVista's default `vtkDataSetMapper` honours clipping planes perfectly well.

## Style

Type hints everywhere, `mypy --strict`. `ruff` for lint and format. Dataclasses (frozen where they are
values). `Result`-style returns for expected failures across a port; exceptions only for programmer
error. Structured logging; never log keys, user images, or whole meshes. Comments explain *why*.

## Commands

(Filled in once the package exists — `uv sync`, `pytest -m "not integration"`, `ruff check`, `mypy`.)
