# ADR-0007 — Python-first, single process. Supersedes ADR-0002 and ADR-0003

- Status: **Accepted**
- Date: 2026-09-23
- Supersedes: ADR-0002 (CAD kernel), ADR-0003 (UI framework and viewport)

## Context

The project was specified as a C#/.NET desktop application. Three constraints were then lifted by the
owner: outcomes matter above all else, everything may be open source including copyleft, C++ is
acceptable, and the owner does not know C# so the language choice carries no learning-curve benefit.

That made the original premise worth re-testing rather than defending. The evidence from this session,
all measured rather than assumed:

**The C# plan had already become a thin shell around Python.** Python was a hard dependency for AI
generation (every open image-to-3D model is Python), for photogrammetry (COLMAP/OpenMVS), and — after
spike S1 — for filleting. C# retained the UI, booleans, file IO and orchestration, and paid an
integration tax on everything else.

**The pure-C# CAD kernel cannot fillet.** Spike S1: CADability 1.1.13 fillets exactly one edge once.
Two or more edges returns null, a second pass throws `NullReferenceException`, and chamfer returns
null in every case tested. The package ships no XML documentation.

**The C# viewport rides on abandoned foundations.** HelixToolkit's WPF line is maintenance-only, its
Avalonia line forced us to pin Avalonia 11 because 12 breaks it, and both depend on SharpDX 4.2.0,
abandoned in 2019.

**The Python equivalents do all of it, better.** Spike S7 re-ran the failing tests:

| Operation | C# / CADability | Python / OCCT |
|---|---|---|
| Boolean subtract | 6429.20 exact | 6429.20 exact |
| Fillet 4 edges at once | **null** | **7931.33 in 4 ms** |
| Fillet all 12 edges | **null** | **7804.70 in 7 ms** |
| Sequential fillets | **throws** | works |
| Chamfer | **null** | 7840.00 exact |
| 3MF export | hand-rolled zip | native |

Crucially, **Python gets C++ performance without a C++ toolchain**: OCCT, VTK, NumPy and Open3D all
ship as precompiled binary wheels. `build123d` + OCCT 8.0.1 installed on the machine's Python 3.14 in
one command, with no compiler present. This directly answers the owner's concern about not having C++
SDKs — they are never needed.

## Decision

**ModelPop is a Python application, in one process, with no sidecar.**

| Concern | Choice |
|---|---|
| Language | **Python 3.12** for the app (3.14 works for CAD, but the AI stack needs 3.12) |
| CAD kernel | **build123d 0.13.0** over **cadquery-ocp 8.0.1** (OCCT 8.0.1), Apache-2.0 |
| Viewport | **PyVista 0.49 / VTK 9.7**, BSD |
| Desktop shell | **PySide6** (Qt 6, LGPL — fine, we are not distributing commercially) |
| Mesh processing | trimesh, manifold3d, Open3D, **PyMeshLab**, **pymeshfix** |
| AI generation | TRELLIS.2 / TripoSG in a **separate 3.12 venv**, driven as a job |
| Vision / scale | OpenCV (ArUco/ChArUco), SAM |
| Reconstruction | COLMAP + OpenMVS via subprocess, pycolmap |
| Slicing | Bambu Studio CLI via `subprocess` — unchanged, ADR-0006 still holds |
| Testing | pytest, **hypothesis** (property-based), syrupy (snapshots), pytest-cov, **import-linter** (layering rules), mutmut |
| Typing / lint | mypy strict, ruff |

### The copyleft unlock

Because the app is open source and not sold, GPL and AGPL dependencies are now available. This is a
material gain, not a footnote:

- **pymeshfix** (AGPL) — the best automatic watertight mesh repair available.
- **PyMeshLab** (GPL) — the full MeshLab filter set.
- **bpy** (GPL) — Blender as a library: voxel remesh, booleans, decimation. Blender 5.2 is installed.
- **CGAL** — via Python bindings, for robust geometry.

The one exclusion that does **not** change: the **Hunyuan3D** family stays out. Its licence excludes
the UK territorially, which is unrelated to commercial use. TRELLIS.2 and TripoSG (both MIT) remain
the generators per ADR-0004.

## Consequences

**Good.**
- One language, one process. The sidecar, its job protocol, supervision, cancellation plumbing and
  the STEP round-trip for fillets all **disappear from the design**. That is a large amount of work
  deleted, not deferred.
- Fillets and chamfers work, on many edges, in milliseconds.
- Native 3MF export; the hand-rolled zip is only needed for Bambu's project-specific sidecar files.
- The entire AI, vision and photogrammetry ecosystem is first-class rather than remote.
- C++ performance with no C++ toolchain.
- Far more example code exists for this exact domain, which matters since the owner will be reading it.

**Cost and risk.**
- **Weaker static guarantees than C#.** Mitigated deliberately: mypy in strict mode, ruff, and
  `import-linter` to enforce the layering that ArchUnitNET would have enforced. The SOLID and testing
  commitments in `04-engineering-standards.md` carry over unchanged; only the tools differ.
- **Desktop UI is less polished than a native XAML stack.** PySide6 is mature and capable, but Qt in
  Python is a smaller world than WPF/Avalonia. Accepted: the UI is not where this product's value is.
- **VTK rendered on the integrated GPU in the spike**, not the RTX 4090 (28–36 FPS at 983k triangles).
  Open item for Phase 1: force the discrete GPU and re-measure. Not architecture-changing.
- **Two Python environments** — 3.12 for the app, and a separate one for PyTorch-based generation.
  Simpler than the C# plan's process supervision, but not zero.
- Packaging a distributable is messier than .NET. Irrelevant here: one user, one machine.

**What survives unchanged.** Almost everything. ADR-0001 (the command bus), ADR-0004 (generator
licences), ADR-0005 (BYO keys), ADR-0006 (slicing out of process), the whole phase plan, all three
generation pipelines, the print-prep pipeline, the readiness score, discovery and remix, G-code
verification and the virtual printer are all language-agnostic. The Bambu CLI spike transfers
directly, and gets simpler: Python's `subprocess` with a list argument avoids the `ArgumentList`
quoting trap that bit the C# version.

**Reversal cost.** Low for a while. The domain model and pipelines are the asset; they are portable.
If Python proves wrong, the plan survives the move back.
