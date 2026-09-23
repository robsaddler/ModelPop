# ModelPop — Generation and print-prep pipelines

Grounded in the research digest (`docs/research/findings.md`). The headline finding from the 2026
literature and from 3D-printing communities: **no AI generator produces print-ready geometry
unassisted.** Output is optimised for how a model *renders*, not how it *slices*. The value ModelPop
adds is not calling a generator — anyone can do that — it is everything after.

The three failures users complain about most, and our answer to each:

| Complaint | Our answer |
|---|---|
| "Detail is in the texture, the print is a smooth blob" | Texture → displacement bake (stage P5) |
| "Wrong size, I had to guess the scale" | Scale is a first-class input: marker, known object, or a stated dimension |
| "Won't slice — holes, flipped normals, self-intersections" | Hard watertight gate before anything reaches the slicer |

---

## The router

The first step is never generation. It is classification plus **dimension extraction**.

```
user prompt (+ images) ──► IntentRouter ──► MECHANICAL ──► Pipeline A (CAD code)
                                        ──► ORGANIC    ──► Pipeline B (mesh generation)
                                        ──► REPLICA    ──► Pipeline C (reconstruction)

        and, before any of them, always:  ──► Pipeline D (search existing models)
```

**Search first.** For most requests, an existing human-made model beats anything we can generate, and
costs no GPU time and no credits. The gallery is offered before generation for every ORGANIC request
and any MECHANICAL one that sounds like a common object. See `07-discovery-and-remix.md`.

- **MECHANICAL** — exact dimensions, holes, mates, fits. "a bracket", "a holder for X", "an enclosure".
- **ORGANIC** — figurines, busts, toys, ornaments, sculpted shapes.
- **REPLICA** — the user supplied photos or video of a real object.

The router also emits a **dimension table**: named parameters with values, units and tolerances. Every
2026 benchmark says LLMs get global shape right and precise dimensions wrong, so we extract the numbers
explicitly and then *verify against them* rather than trusting the model to have honoured them.

If intent is ambiguous, ask exactly one question. Never silently guess between paradigms.

---

## Pipeline A — CAD code (mechanical parts)

This is the pipeline that produces genuinely useful functional parts, and it is where ModelPop can be
better than every consumer AI-3D product, none of which do it well.

```
dimension table ──► LLM writes build123d code ──► execute in-process ──► export STEP+STL
                          ▲                                │
                          │                                ▼
                          │                         GATES (in order)
                          │                    1. compiles / executes
                          │                    2. STL non-empty
                          │                    3. watertight + manifold
                          │                    4. topology sane (Euler χ, component count)
                          │                    5. DIMENSIONS match the table ± tolerance
                          │                    6. VLM reads an orthographic contact sheet
                          │                                │
                          └────── failing gate text ◄──────┘   max 4 iterations, keep best
```

Design notes that come straight from what works in the field:

- **Target build123d** (OCCT-backed, gives STEP, fillets, chamfers) — and it is now the app's own CAD
  kernel, so generated code and hand editing share one representation. No translation layer.
- **Give the model a helper library**, not a bare API: `thru_hole()`, `counterbore()`, `standoff()`,
  corner-relative placement. Published results show helpers improve success more than a bigger model.
- **Render an orthographic contact sheet** (top/front/right/iso in one image), not a pretty isometric.
  Orthographic views are what vision models judge accurately.
- **Feed back measurements, not vibes.** Gate 5 measures the actual solid and reports
  "hole spacing is 31.4 mm, spec says 32.0 ± 0.2" — a textual, numeric correction. Evidence says image
  feedback barely helps *editing*; numbers do.
- One feature at a time, measure after each. Do not let the model emit 80 lines then hope.

**Output is a STEP plus a feature-parameter set**, so the user can then change "32 mm" to "35 mm" and
rebuild — real parametric editing, not mesh pushing.

---

## Pipeline B — mesh generation (organic)

```
text ──► [local T2I: FLUX/SDXL] ──► image ──► background removal ──► ModelGenerator  ──► raw mesh
image ─────────────────────────────────────►                                              │
                                                                                          ▼
                                                                              Print-prep pipeline (below)
```

Every strong open image-to-3D model in 2026 is **image-conditioned**, so text-to-3D is really
text → image → 3D. That is an advantage: the user gets to approve the reference image before we spend
GPU minutes on a mesh, which is a far better UX than a slow mystery box.

Generator choice is a licence decision as much as a quality one — see ADR-0004. Since you are in the UK,
the Tencent Hunyuan3D community licence (which excludes the UK) rules out the otherwise-obvious first
choice. **TRELLIS.2 (MIT) is the primary; TripoSG (MIT) is the lightweight fallback.** TRELLIS.2 models
open surfaces by design, so its output is *not* reliably watertight — the repair gate is mandatory, not
optional.

A generate → critique → regenerate loop scores candidate meshes on multi-view renders and geometric
sanity, keeps the best of N, and shows the user the runners-up.

---

## Pipeline C — photos/video → scaled replica

```
photos/video ──► frame selection (blur reject) ──► SAM segmentation ──► COLMAP poses
                                                                          │
                          ArUco / ChArUco board detected in frames ───────┤
                                                                          ▼
                                            OpenMVS dense + mesh ──► Umeyama fit ──► METRIC mesh
                                                                          │
                                                                          ▼
                                                              Print-prep pipeline (below)
```

- **Scale is the differentiator.** Photogrammetry never recovers metric scale on its own. A printed
  ArUco/ChArUco mat in shot gives sub-1% error and doubles as camera calibration. Fallbacks, in order:
  known object (credit card, ISO ID-1 85.60 × 53.98 mm), user states one dimension, metric depth model.
- Ship a **printable calibration mat** as the app's first suggested print. It is a delightful first-run
  experience and it makes every later capture better.
- For sparse or incomplete captures, hallucinate the unseen side with a multi-view generator and
  align it to the partial scan — but always show the user which parts are measured and which are invented.

---

## The print-prep pipeline (shared by all three paths)

Ordered stages, each a `PipelineStage`, each independently testable, each able to
report what it changed. The user can inspect and re-run from any stage.

| # | Stage | What it does | Fails how |
|---|---|---|---|
| P1 | **Normalise** | units → mm, merge vertices, drop tiny islands, recompute normals | never (best effort) |
| P2 | **Repair** | fill holes, fix self-intersections, fix orientation; escalate to voxel remesh if needed | **hard gate**: must end watertight + manifold |
| P3 | **Scale** | apply target dimension from router/marker/user | warns if any feature < nozzle width |
| P4 | **Wall thickness** | SDF thickness map; thicken below 2× line width (0.84 mm @ 0.42), or flag | warns with a heat-map overlay |
| P5 | **Detail rescue** | bake texture luminance/normal → displacement (0.2–0.5 mm), then remesh | organic path only; optional |
| P6 | **Orient + base** | minimise overhang area and support volume, maximise bed contact; optional flat cut | shows the top 3 candidate orientations |
| P7 | **Hollow** | inward offset 2–3 mm + drain holes ≥ 4 mm | busts/large solids only; opt-in |
| P8 | **Decimate** | quadric decimation preserving silhouette, target ~300 k triangles | never |
| P9 | **Split / colour** | separate parts per filament or for bed-fit | optional |
| P10 | **Score** | the readiness report (below) | informational |
| P11 | **Write 3MF** | Bambu project 3MF with per-object settings and support enforcers/blockers | hard gate |
| P12 | **Slice** | Bambu Studio CLI → gcode + telemetry | **verified working**, see spike |
| P13 | **Verify G-code** | parse the toolpath: unsupported islands, bridge spans, first-layer area, thin runs, build-volume extents | see `08-gcode-verification.md` |

**Stage P5 is the one nobody else does.** It is the direct answer to "the printed object is a smooth
blob compared to the render". It is also genuinely hard; treat it as a research spike, not a given.

### The readiness score

No industry-standard metric exists, so define one and make it honest and explainable. It is a report,
not a single number, though it rolls up to a traffic light:

- watertight + manifold (**gate** — red if failed, nothing else matters)
- minimum wall thickness vs nozzle, as a ratio and a heat map
- overhang area above threshold angle, and estimated support volume
- base contact area vs height (tip-over risk)
- bounding box vs the P2S 256 × 256 × 256 mm volume
- triangle count and file size
- slicer telemetry after P12: predicted time, bridge seconds, any `warning_message` shown verbatim
- G-code verification after P13: unsupported islands (red), bridge spans, first-layer contact area

Every red or amber item links to the stage that can fix it, and to a one-click "fix this".

### Supports: we do not implement them

Tree and organic support generation is thousands of lines of well-tuned slicer code with no embeddable
open library. We generate **support enforcer and blocker volumes** as extra parts in the 3MF and let
Bambu Studio do the actual support generation. This is the correct boundary, confirmed by the spike:
`--enable-support --support-type "tree(auto)"` worked, and the sliced 3MF reported `support_used="true"`.

---

## Evaluating our own output

The app must be able to judge a result without the user. Combine three cheap, independent signals:

1. **Geometric sanity** — watertight, Euler characteristic, component count, bbox. Catches what vision
   models miss. Free.
2. **Embedding similarity** — 3D/text similarity on sampled points. Cheap pre-filter for "is this even
   the right object".
3. **Vision-model judgement** — multi-view renders scored against the prompt with a generated yes/no
   question catalogue. Correlates well with human judgement; costs credits.

This is the same harness that backs the offline eval suite (`tests/ModelPop.Ai.Evals`), so improving
the product and measuring the product are the same work.
