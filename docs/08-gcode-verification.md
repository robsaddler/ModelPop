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
