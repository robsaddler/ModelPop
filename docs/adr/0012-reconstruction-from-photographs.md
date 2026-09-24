# ADR-0012 — Photographs are *measured*, not generated, and it stays a separate path

**Status:** Accepted
**Date:** 2026-09-24
**Evidence:** `docs/research/spike-photogrammetry.md` — every stage run by hand against the real
tools before a line of the adapter was written.
**Completes:** Phase 7 of `docs/00-plan.md`.

## Context

ModelPop already turns *one* photograph into a model: a generative model looks at it and invents a
plausible shape, including a back it has never seen. That is useful and it is a guess.

Several photographs are a different proposition entirely. With enough overlap the geometry is
*determined* — the camera positions can be solved, and the surface measured. Same input medium,
same output type, completely different epistemic status. The question this record settles is how
much they should share.

## Decision

### 1. A separate port, a separate menu entry, separate words

`MeshGenerator` and `PhotoReconstructor` are different ports, not one with a flag. The menu says
"Make one from a **picture**" and "**Measure** one from several photographs". The provenance on the
finished model says "Generated from…" or "Reconstructed from 24 of 24 photographs…".

This costs a little duplication and buys the one thing that matters six months later: whether the
shape in front of you was measured or invented. Blurring them behind a single "photos → model"
button would make that unanswerable, and the answer changes whether you would trust the part.

### 2. COLMAP and OpenMVS, driven as subprocesses

Both are external programs rather than libraries, so this is the same adapter shape as the slicer
(ADR-0006) and the mesh generator (ADR-0010). Licences: COLMAP is **new BSD**, OpenMVS is
**AGPL-3.0**. AGPL is fine twice over here — the application is open source and not sold, and it
drives the binary as a separate process rather than linking it.

Neither is in winget; both are unzipped GitHub release builds, which is why discovery checks an
environment variable, then `C:\Tools`, then `PATH`.

### 3. The pipeline is judged by its output, not by its exit codes

Written against measured behaviour, because the documentation is wrong about the parts that matter:

- **The mapper has two failure modes and only one is loud.** Photographs with nothing to match exit
  non-zero. Photographs that match but will not connect into one scene exit **zero** having written
  no model at all. Both mean the same thing to the user, so the stage is judged by whether a model
  directory appeared, and both get the same sentence about overlap and texture — never COLMAP's own
  words.
- **OpenMVS says nothing on stdout.** It writes a timestamped log into the working directory, so the
  working directory is set deliberately and the log is read when a stage fails.
- **COLMAP 4.2 renamed its options**: `--FeatureExtraction.use_gpu`, not the name in every tutorial.

### 4. Progress is weighted by measured time

Densification alone is two thirds of a run; the five COLMAP stages together are under five percent.
An evenly spaced bar sits at 14% for three seconds and then appears to hang for a minute, which is
exactly when people kill a job that is working. So `Stage` carries a measured weight.

### 5. One GPU lease, shared, and therefore a port

Densification and mesh generation both want most of a 16 GB card. They must share **one** lease —
but `modelpop.generation` and `modelpop.vision` are peers in the layering and neither may import the
other. `import-linter` caught this the moment the second user appeared, which is what it is for. The
lease became a port in `application`, and the composition root hands the one implementation to both.

### 6. What it does *not* do

**No background removal.** A capture of an object on a table is also a capture of the table. The
adapter keeps the largest connected piece and reports what fraction it discarded, because losing
half the surface is either exactly right or completely wrong and only the user knows which.
Segmentation is a better answer and is not built.

**No scale recovery from the photographs themselves.** A reconstruction is a shape without units.
The size is stated, and a size *measured* against a reference reads differently in the note from one
the app chose — the same distinction the single-photo path already makes.

**No texturing.** `TextureMesh` is the obvious next stage and would feed straight into detail rescue,
which is a genuinely appealing combination. Not built, because Phase 7 does not need it and it adds
minutes to every run.

## Consequences

- ModelPop can now measure a real object and print a copy of it, which was the last headline gap.
- The result is **not watertight** by nature — photogrammetry sees only what the camera saw — so it
  arrives in the ordinary repair-and-readiness pipeline like any imported mesh, and says so.
- Roughly a third of a million triangles arrive by default. The adapter says so rather than
  decimating: throwing away two thirds of a measurement somebody waited minutes for, unasked, is the
  wrong default when Simplify is one button away.
- A capture that fails costs minutes to discover. The cheap checks — how many photographs, are they
  still on disk — happen first, in the domain, in microseconds.
