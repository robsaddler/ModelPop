# ADR-0002 — CADability is the in-app CAD kernel; build123d generates, STEP is the bridge

> **SUPERSEDED by ADR-0007 (Python-first).** Kept for the reasoning and for spike S1's measured findings about CADability. The project no longer uses a C# CAD kernel; fillets are handled natively by build123d/OCCT in-process.

- Status: **Accepted, amended by spike S1** — fillet/chamfer move to the sidecar
- Date: 2026-09-23

> **Spike S1 result (`docs/research/spike-s1-cadability.md`): PARTIAL PASS.**
> CADability's booleans are **exact** (box − cylinder = 6429.20, expected 6429.20), primitives are
> exact, and STEP export/re-import and STL export all work. **But fillet and chamfer are not usable:**
> `RoundEdges` succeeds for exactly **one edge, once** — two or more edges returns null, a second pass
> throws `NullReferenceException` inside the library, and `ChamferEdges` returns null in every case
> tested. **Amendment: fillet and chamfer are routed to the OCP/build123d sidecar** (OCCT
> `BRepFilletAPI`), exchanged over STEP. Everything else stays in CADability, in-process.

## Context

The app must contain a real parametric CAD editor **in-process** — sketches, extrude/revolve/sweep/loft,
booleans, fillets and chamfers, a feature history that rebuilds, STEP import/export, and tessellation
to STL/3MF. Everything must be open source and free.

The routes surveyed:

| Route | Verdict |
|---|---|
| **CADability** (`FriendsOfCADability/CADability`) | **MIT**, pure managed C#, `netstandard2.0`, NuGet 1.1.13 published 2026-09-18, repo committed to almost daily through Sept 2026. Its own B-rep kernel with real `Solid.Union/Intersect/Subtract` via an internal `BRepOperation`, fillet/chamfer constructors, path extrude and rotate, ruled solids, and built-in STEP/STL/DXF import-export. Core library is separable from its WinForms UI. Has a dimension-driven parametric layer. |
| **Macad.Occt** (from Macad3D) | MIT, already targets **.NET 10** + OCCT 7.9.2, comprehensive C++/CLI wrapper. But it is **not published to NuGet** — it means cloning a monorepo and owning a C++/CLI build forever. |
| **Occt.NET** (NuGet) | Turnkey and comprehensive, but has **no declared licence** and bundles FFmpeg, Qt5, FreeImage, OpenVR and TBB with no compliance documentation. Excluded on legal hygiene. |
| CascadeSharp | Archived 2026-06. Dead. |
| Fornjot (Rust) | Archived; "no longer in development". Dead. |
| Chili3D | AGPL-3.0, browser-only, no .NET binding. |
| SolveSpace / `libslvs` | GPL-3.0 — usable for constraint solving only if the app is GPL. `planegcs` (LGPL) is friendlier but has no C# binding. |
| Truck (Rust), libfive | No .NET binding; DIY FFI. |
| build123d / CadQuery over OCP | Apache-2.0, genuinely mature, freshly released against OCCT 8.0.1 — but out-of-process, which the constraint forbids for *editing*. |

## Decision

**Two kernels, each where it belongs, with STEP as the interchange format.**

1. **CADability, in-process, is the interactive CAD editor.** All user editing and all LLM-driven
   editing commands execute against it. This satisfies the "CAD tools in the app" constraint with an
   MIT, pure-managed, actively maintained dependency and **no native interop risk at all**.

2. **build123d in the Python sidecar generates parametric parts** for Pipeline A (the AI CAD-code
   path). The sidecar exists anyway for mesh generation, so this costs nothing extra. It emits
   **STEP**, which CADability imports for editing.

Both sit behind `ICadKernel`. Nothing in `Application` knows which one produced a solid.

## Consequences

**Good.**
- No C++/CLI build, no native DLL marshalling, no crash-the-UI class of bug in the editing path.
- AOT- and debugger-friendly; a pure-managed kernel is vastly easier to test and to step through.
- We get OCCT's maturity where it matters most (generated parts, complex STEP handling) without
  taking on OCCT interop, because the sidecar owns it.
- Licensing is clean end to end: MIT + Apache-2.0.

**Cost and risk.**
- CADability's kernel is less battle-hardened than OCCT on pathological imported NURBS. Mitigation:
  every STEP import runs through validation, and import failures fall back to the sidecar's OCCT
  importer, which returns a tessellated or re-exported result.
- Its parametric layer is dimension-driven rather than a general feature tree. **This is acceptable
  because ModelPop's feature history lives in our own command document (ADR-0001), not in the kernel.**
  The kernel is asked to perform operations; it is not asked to remember them.
- Round-tripping through STEP loses nothing geometrically but does lose our feature semantics — so the
  command history stays authoritative on our side, and STEP is used for geometry transfer only.

**Escape hatch.** If CADability proves too limited in practice, `ICadKernel` gets a second adapter over
**Macad.Occt** (MIT, .NET 10, OCCT 7.9.2), accepting the C++/CLI build cost. The port exists precisely
so this is a contained decision rather than a rewrite. Evaluate at the end of Phase 5.

## Amendment (spike S1, 2026-09-23) — the fillet split

`ICadKernel` operations are served by **two** implementations, chosen per operation:

| Operation | Served by |
|---|---|
| primitives, extrude, revolve, sweep, loft | CADability, in-process |
| **booleans** (unite / subtract / intersect) | CADability, in-process — verified exact |
| topology queries, volume, bounding box, tessellation | CADability, in-process |
| STEP import/export, STL export | CADability, in-process — round-trip verified |
| **fillet and chamfer** | **OCP / build123d sidecar (OCCT `BRepFilletAPI`), over STEP** |

Consequences of the split:

- Fillet becomes **out-of-process and asynchronous**. Acceptable: it is a modelling command, not a
  per-frame interaction, and ADR-0001 already makes every edit a discrete, undoable command that may
  complete asynchronously.
- The Python sidecar becomes a **hard dependency for CAD editing**, where it was previously optional
  and only needed for generation. This is the real cost of the amendment and should be stated plainly
  in the installer and first-run experience.
- No external CAD *application* is involved, so the "CAD tools live in the app" constraint holds. The
  sidecar is our own process, shipped with the product.
- Still entirely open source: CADability MIT, build123d and OCP Apache-2.0.

**Escape hatch, unchanged but now more likely to be needed.** If the STEP round-trip latency makes
interactive filleting feel bad in Phase 5, add a second `ICadKernel` adapter over **Macad.Occt**
(MIT, .NET 10, OCCT 7.9.2) and accept a C++/CLI build in the toolchain. Decide with measurements.

**Also do:** file an upstream issue against CADability with the minimal fillet repro. The project is
MIT and was committed to on the day of the spike, so it may simply get fixed — but the schedule must
not depend on that.

**Gotcha to remember:** `Make3D.MakeCylinder(location, radiusVector, axisVector)` takes the **radius
before the axis**, and the package ships **no XML documentation**. Confirm every signature by
experiment; a wrong parameter order silently produces wrong geometry that makes correct code look broken.
