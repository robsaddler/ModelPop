# G-code verification — will this print actually work?

Added at your request. Worth stating the physics plainly first, because it changes what we should
actually build.

## The honest answer on nozzle-vs-printed-part collision

In normal layer-by-layer printing, **the nozzle cannot hit already-printed geometry**, and this is
structural rather than lucky. The printer builds strictly bottom-up: the nozzle is always at the height
of the layer it is currently laying down, and everything already printed is *below* it. There is
nothing above the nozzle to hit.

So the naive fear is not the real risk. The real risks are these, and they are worth checking:

| Risk | Real? | Where it bites |
|---|---|---|
| **Sequential / by-object printing** | **Yes, genuinely** | When printing objects one at a time to completion, the gantry and the nozzle *can* strike a finished object. This is the one true collision case. |
| **Curling and warping lifting a part into the nozzle path** | **Yes** | An overhang or corner curls upward above the current layer, the nozzle clips it, and the print is knocked loose. Not predictable from geometry alone — it depends on material, cooling and geometry together. |
| **Unsupported islands** | **Yes, very common** | A layer contains a region with nothing beneath it. It gets extruded into thin air, drags, and becomes a bird's nest. This is the most common real slicing failure. |
| **Excessive bridge spans** | Yes | Long unsupported spans sag or fail. |
| **Part taller than gantry clearance** | Yes | Rare on a P2S with a full-height enclosure, but check it. |
| Nozzle hitting the current object | **No** | Physically precluded by bottom-up building. |

Bambu Studio already performs the sequential-print clearance check. **We surface its warnings verbatim
rather than duplicating the check** — this is the same boundary as ADR-0006.

## What we do build: stage P13, G-code verification

After slicing (stage P12), parse the produced G-code and reconstruct the per-layer toolpath. This is
cheap, deterministic, needs no GPU, and catches real failures.

| Check | Method | Verdict |
|---|---|---|
| **Unsupported islands** | For each layer, project the extruded regions onto the layer below. A region whose overlap with the layer below is below a threshold, and which has no support beneath it, is an island. | **Red.** Offer: add a support enforcer there, re-orient, or accept. |
| **Bridge spans** | Measure unsupported span length per bridging move. | Amber above a material-dependent threshold. |
| **First-layer contact area** | Sum first-layer extrusion area; compare with model height. | Amber for tip-over risk. Offer a brim. |
| **Thin features** | Any extrusion run narrower than one line width. | Amber; link back to the wall-thickness stage. |
| **Overhang exposure** | Layer-over-layer horizontal offset vs threshold angle. | Informational heat map. |
| **Travel moves over open air** | Long travels crossing the part without a z-hop. | Informational; stringing risk. |
| **Build volume** | Toolpath extents vs 256 × 256 × 256 mm. | Red if exceeded. |
| **Slicer telemetry** | `warning_message`, high `feature_type_times.Bridge`, `generate_support_material_time` of 0 when overhangs exist. | Pass through verbatim. |

Sequential-print collision stays with Bambu Studio; we just report it.

## A layer preview the user can scrub

The reconstructed toolpath is worth showing, not just scoring. A layer slider with the toolpath drawn
and problems highlighted in place is the single most educational feature the app can have for someone
new to printing — it makes "why did this fail" visible instead of mysterious. It also costs little once
the G-code is already parsed for the checks above, and it reuses the viewport.

Colour by feature type using the categories the slicer already reports, so the preview and the
telemetry agree.

## Where this lands

Stage P13 in the print-prep pipeline, and a `GcodeVerification` section in the readiness report.
Build the checks in Phase 2 alongside the slicer adapter; add the scrubable preview in Phase 2 or early
Phase 3 once the viewport is solid.

Every red finding must link to the stage that can fix it and offer a one-click fix. A warning the user
cannot act on is noise.

---

## What was actually built, and what real G-code taught us

Shipped in Phase 2b as `modelpop.printing.gcode`, behind the `GcodeVerifier` port. Two of the checks
above made the cut; the rest did not yet, and one was dropped on reflection.

| Check | Status | Why |
|---|---|---|
| Unsupported islands | **Built** | The failure that actually happens. Proven against a real slice of a floating table top. |
| First-layer contact area | **Built** | Cheap, and the "it fell over at layer 40" failure is preventable with a brim. |
| Build volume | Not needed here | The slicer refuses the plate before any G-code exists, so this would never fire. |
| Bridge spans, thin features, overhang exposure, air travel | Deferred | Each needs a material-dependent threshold we have no evidence for yet. A number invented today would be noise wearing a lab coat. |
| Sequential-print collision | Still Bambu's | Unchanged from above, and unchanged by anything found since. |

### Three things the real files decided

The first parser was written from the generic G-code most tooling assumes, and it was **completely
wrong** — a plain cube came back riddled with floating islands. Inspecting genuine Bambu output fixed
three separate mistakes, each of which alone was enough to make the check useless:

**Extrusion is relative.** The file emits `M83` once at the top. So an extruding move is one with a
positive `E`, not one whose `E` exceeds the last. Read as absolute, *every* move looks like extrusion,
every layer looks solid, and nothing is ever flagged — or everything is.

**Layers are marked, not inferred.** `; CHANGE_LAYER` and `; Z_HEIGHT:` are authoritative. Splitting on
Z changes instead — the obvious approach — manufactures a spurious layer for every travel z-hop, and a
z-hop layer contains one stray point that is unsupported by construction.

**Features are labelled.** `; FEATURE: Support` is the only way to tell support material from the model
it is holding up.

### How it is checked

Each layer is rasterised onto a 1 mm occupancy grid. The accumulated material below is dilated by one
cell — about a nozzle width — and anything in the new layer falling outside it is floating. Material
**accumulates**: a layer is held up by everything printed so far, not just by the layer immediately
below, which matters wherever a wall steps inwards and back out.

Two constants encode judgement rather than physics: 6 mm² before a floating patch counts as a failure
rather than a stray blob, and four findings before the panel stops listing them individually. Both are
there to keep the check from crying wolf, which would be worse than not having it.

### Proven against the real slicer

Two integration tests do the work a synthetic fixture cannot. One reads back a genuine slice to check
the dialect assumptions still hold. The other slices the same table twice, with and without supports,
and requires the floating top to be caught the first time and clean the second — so the check is shown
to **discriminate**, not merely to complain.

