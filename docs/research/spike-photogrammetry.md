# Spike — COLMAP + OpenMVS end to end — WORKS, and the timings are lopsided

Run on this machine, 2026-09-24, against 24 rendered views of a lumpy textured sphere at
1024x768. This is the evidence behind the Phase 7 reconstruction adapter: every stage was run by
hand first, and the adapter was written to what they actually did rather than to their
documentation.

## The pipeline, and what each stage cost

| # | Stage | Tool | Time | Produced |
|---|---|---|---|---|
| 1 | `feature_extractor` | COLMAP | 0.6 s | ~2,500 SIFT features per image |
| 2 | `exhaustive_matcher` | COLMAP | 0.2 s | match graph in the database |
| 3 | `mapper` | COLMAP | 2.5 s | **23 of 24** images registered, `sparse/0/` |
| 4 | `image_undistorter` | COLMAP | 0.2 s | `dense/` in COLMAP layout |
| 5 | `InterfaceCOLMAP` | OpenMVS | ~1 s | `scene.mvs` |
| 6 | `DensifyPointCloud` | OpenMVS | **~60 s** | 47 MB dense cloud |
| 7 | `ReconstructMesh` | OpenMVS | ~20 s | **638,938 faces** |

**Total about 90 seconds.** Densify alone is two thirds of it and the mesh stage most of the rest;
the five COLMAP stages together are under 5%.

That lopsidedness is why progress is reported against *measured* stage weights rather than
"stage 3 of 7". An evenly-weighted bar sits at 14% for three seconds and then appears to hang for a
minute, which is exactly the shape that makes people kill a job that is working.

## What the output is actually like

```
319,526 vertices, 638,938 faces
watertight: False          winding consistent: True
connected pieces: 1
```

Three properties that shape the adapter:

- **Not watertight.** Photogrammetry sees what the camera saw; wherever it did not look there is a
  hole. The result goes through the existing repair path like any imported mesh, and the readiness
  panel will say so.
- **Arbitrary scale.** The bounding box came back about 1.8 units across for an object that was
  2 units across in the scene. There is no relationship to millimetres at all, which is the same
  problem single-photo generation has, and it gets the same answer: the size is stated, and a size
  *measured* against a reference reads differently from one the app chose.
- **One connected piece here, but that is luck.** A real capture picks up the table, the operator's
  shoes and a patch of wall. The adapter keeps the largest connected piece and says how much it
  discarded.

## Traps, measured not assumed

1. **`mapper` writes to a numbered subdirectory.** `sparse/0`, not `sparse`. It can produce several
   disconnected models when the photos do not all overlap, and it produces *none* when
   reconstruction fails — while still exiting **0**. The adapter globs for models, picks the one
   with the most registered images, and treats "no model directory" as the real failure it is.
2. **COLMAP 4.2 renamed its options**: `--FeatureExtraction.use_gpu`, not
   `--SiftExtraction.use_gpu`. The old name is rejected outright.
3. **OpenMVS prints nothing to stdout.** It writes a timestamped `.log` into the *working
   directory* and exits 1 even for `--help`. On real work it does exit 0 on success, so the exit
   code is usable — but the only diagnosis of a failure is in that file, so the adapter sets the
   working directory deliberately and reads the newest log when something goes wrong.
4. **Roughly a third of a million vertices arrives by default**, which is past what the viewport
   draws comfortably. The adapter *says so* rather than decimating: throwing away two thirds of a
   measurement the user waited minutes for, without asking, is the wrong default. Simplify is a
   button away.

## What this does not tell us

The test images are renders: evenly lit, noise-free, with perfect texture and known-good coverage.
Real photographs are worse in every one of those ways, and the stage that will suffer is `mapper` —
if it registers only a handful of the photos, everything downstream is built from a bad pose graph.
That is why the adapter reports **how many photos were registered out of how many were given**,
which is the single number that says whether a capture was good enough.
