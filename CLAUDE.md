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
- **VTK picking: 0.0045 ms** with a cached `vtkCellLocator`. The viewport runs on the **Intel iGPU**
  and cannot be moved off it from inside the app — and does not need to be: re-measured in a real
  window at **70–96 FPS on 393k triangles and 50 FPS on 1.57M**, well past the display budget.
  Settled; see `docs/research/spike-viewport-gpu.md`.
- RTX 4090 Laptop has **16 GB VRAM**. One large generative model at a time.
- **COLMAP 4.2.0 (CUDA) at `C:\Tools\COLMAP\bin\colmap.exe`** and **OpenMVS 2.4.0 at
  `C:\Tools\OpenMVS`** — installed 2026-09-24 for Phase 7. Neither is in winget; both are
  unzipped GitHub release builds. COLMAP's GPU SIFT is verified working on the 4090 (8 images in
  0.18 s). Licences: COLMAP new BSD, OpenMVS AGPL-3.0 — both fine, and both run as subprocesses.
- **COLMAP 4.2 renamed its options.** It is `--FeatureExtraction.use_gpu`, not
  `--SiftExtraction.use_gpu` as every tutorial online still says; the old name is rejected outright.
- **OpenMVS writes no stdout.** Like the Bambu CLI, it logs to a timestamped `.log` file in the
  working directory and exits **1** even for `--help`. Read the log, not the pipe. On real work
  it does exit 0 on success.
- **COLMAP's mapper has two failure modes and only one is loud.** Photographs with nothing to
  match exit non-zero; photographs that match but will not connect into one scene exit **zero**
  having written no model at all. Judge it by whether `sparse/<n>/` appeared, never by the exit
  code. It writes to a *numbered* subdirectory and can write several - take the largest.
- **Photogrammetry timings are lopsided**: densify is two thirds of a run, meshing most of the
  rest, the five COLMAP stages together under 5%. Weight any progress bar by that, or it sits
  still for a minute in the middle and people kill a job that is working.

## The interface thread

**Nothing but interface work runs on it.** Every slow thing here is a subprocess or a socket - a
CAD rebuild, a slice, a generation, a reconstruction, a job sent to a printer - and each would
freeze the window for as long as it takes. They go through `BackgroundRunner`; there is no second
way of doing it.

Two corollaries, both learned by shipping them broken:

- **Anything a view-model announces must cross back through a Qt signal** before it touches a widget
  or VTK. A bound method runs on whichever thread did the work.
- **Never probe something expensive from a `can_*` property.** The interface asks those on every
  refresh. `Build123dKernel.is_available` started an interpreter and imported OCCT; one click on
  *Sphere* spawned eleven of them, on the interface thread, for twenty-three seconds. Settle it once
  and remember it.

A comment saying "inline for now, this is fast enough" is how both of these survived: it was true in
Phase 1 and nobody revisited it when slicing, generation and photogrammetry were built behind the
same seam. `tests/geometry/test_thread_affinity.py` now asserts the rule rather than describing it.

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
   signal. **`MainWindow` was wired straight to bound methods and this trap was rediscovered the
   expensive way**: a CAD rebuild finished on its worker, handed the mesh to the workspace
   view-model, and the window's listeners touched VTK from there. Geometry drew wrong and the next
   orbit deadlocked the process - 57 threads all in Wait, 7 s of CPU between them. Every callback a
   view-model is given must be `signal.emit`, never a method; `tests/geometry/test_thread_affinity.py`
   now asserts it. No unit test could catch it, because they all use the inline runner.
8. **VTK does not fail on a GPU-less runner, it takes the process down** with an access violation.
   Hence the `renders` marker, deselected in CI.
9. **Off-screen `Plotter.screenshot()` hands back the previous buffer** after a change that does not
   dirty the scene graph - a clipping plane, for one. The picture never appears to move and the
   feature looks broken when it is not. Call `render()` first, every time.
10. **Measure a rendered change between two screenshots, not against the background.** The viewport
   background is a gradient, so "differs from the top-left pixel" counts most of the sky as drawn
   and swamps the model. That reading cost an hour and nearly bought a mapper swap that fixed
   nothing - PyVista's default `vtkDataSetMapper` honours clipping planes perfectly well.
11. **This display scales at 150%, so the VTK render window is 1.5x the Qt widget.** Measured: a
   700x600 `QtInteractor` owns a 1050x900 render window. `vtkCoordinate` returns *render window*
   pixels, so any synthetic Qt mouse event built from a world position must be divided by that
   ratio first. Getting it wrong sends the click hundreds of pixels away and every widget under
   test looks broken while working perfectly - it cost most of a debugging session on the drag
   handles, which turned out never to have been faulty.
12. **PyVista's `AffineWidget3D` is gone, and is not to come back.** Its plumbing works - hover,
   press, drag, release all verified by hand - but three things in its own source make it unusable:
   `_get_world_coord_trans` is documented as "not physically accurate" and "ignores zoom" and
   scales by `actor_length * 2`; its handle actors get a transform once at construction and never
   follow the part; and `always_visible` draws them with a -20000 polygon offset, which smears
   them over the model. `modelpop.rendering.drag_handles` replaces it with ray-to-axis closest
   approach and ray-plane intersection, which track the cursor exactly at any zoom.
13. **A gizmo must pivot where the command pivots.** `Rotate` compiles to build123d's `Rot`, which
   turns the part about the **world origin** - not its centre. Previewing a rotation about anything
   else shows one thing, rebuilds another, and emits a spurious `Move` next to every `Rotate`.
14. **The test suite runs Qt under `QT_QPA_PLATFORM=offscreen`** (set in `tests/conftest.py`). An
   *embedded* VTK render window gets no surface there and reports a size of `(0, 0)`, so every
   world-to-screen conversion collapses to zero. Tests that drive a `QtInteractor` with real mouse
   events must skip unless a real platform is in use: `QT_QPA_PLATFORM=windows pytest -m renders`.
   Driving synthetic input at a real window also prints `Windows fatal exception: code 0x8001010d`
   (`RPC_E_CANTCALLOUT_ININPUTSYNCCALL`) - noise, not a crash.
15. **`pytest -m renders` exits 127 after every test passes.** VTK teardown takes the process down
   once the run is over. Confirmed pre-existing and independent of any one test file, so judge that
   run by its reported results, not its exit code.

## Style

Type hints everywhere, `mypy --strict`. `ruff` for lint and format. Dataclasses (frozen where they are
values). `Result`-style returns for expected failures across a port; exceptions only for programmer
error. Structured logging; never log keys, user images, or whole meshes. Comments explain *why*.

## Commands

(Filled in once the package exists — `uv sync`, `pytest -m "not integration"`, `ruff check`, `mypy`.)
