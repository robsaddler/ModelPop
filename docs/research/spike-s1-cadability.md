# Spike S1 — CADability as the in-app CAD kernel — PARTIAL PASS

Run on this machine, 2026-09-23, against **CADability 1.1.13** (MIT, pure managed, netstandard2.0) on
.NET 10. Gate for ADR-0002.

**Verdict: the kernel is sound for everything except filleting and chamfering, which are not usable as
shipped. ADR-0002 needs amending — see the recommendation at the bottom.**

## What works, and works exactly

| Capability | Result |
|---|---|
| `Make3D.MakeBox` 20³ | volume **8000.00** — exact |
| `Make3D.MakeCylinder` r5 h30 | volume **2356.19** vs π·25·30 = 2356.19 — exact |
| `Make3D.MakeSphere`, `MakeCone` | construct fine |
| **`Solid.Subtract`** (box − cylinder) | 1 solid, volume **6429.20** vs expected 6429.20 — **exact**, 8 faces, 18 edges |
| **`Solid.Unite`** (two boxes) | volume **16000.00** — exact |
| `Solid.Volume`, `Edges`, `Shells`, `GetBoundingCube` | correct |
| **`ExportStep.WriteToFile`** | 7,710-byte STEP written |
| **`ImportStep.Read`** round-trip | returns 1 `Solid` |
| **`PaintToSTL`** | STL written |

The boolean engine is genuinely accurate, including on the geometry it produces itself. That is the
hard part of a B-rep kernel and CADability gets it right.

Also present in the API: `MakePrism` (extrude), `MakeRevolution`, `MakePipe` (sweep),
`MakeRuledSolid` (loft), `MakeOffset(Solid, double)` (shell/hollow), `BRepOperation.SplitByPlane`,
`ClipFace`. Not all exercised in this spike.

## What does not work: fillet and chamfer

| Attempt | Result |
|---|---|
| `BRepOperation.RoundEdges`, **1 edge**, fresh box, r=2 | **OK** — 7 faces, volume 7982.83 (material removed is exactly (4−π)·r²·h) |
| `RoundEdges`, 1 edge, on a **boolean result** | **OK** — 9 faces, volume 6412.04 |
| `RoundEdges`, **2 edges** at once | **null** |
| `RoundEdges`, 3 or 4 edges at once | **null** |
| `RoundEdges`, **second pass** on an already-filleted shell | **`NullReferenceException` thrown inside the library** |
| `Make3D.MakeFillet`, 4 edges | **null** |
| `BRepOperation.ChamferEdges`, 1/2/3/4 edges | **null in every case** |

So: **you can fillet exactly one edge, exactly once.** The arithmetic is right when it works — it is
the multi-edge and repeat cases that fail, silently returning null or throwing.

A CAD editor where a user cannot round all four corners of a bracket is not a CAD editor. This is a
blocking gap for Phase 5, not a cosmetic one.

## API gotcha that cost real time

`Make3D.MakeCylinder(GeoPoint location, GeoVector radiusVector, GeoVector axisVector)` —
**the radius comes before the axis**, which is the reverse of the intuitive reading and of most kernels.
Getting it backwards silently produces a cylinder of the wrong size and shape, which then makes a
perfectly correct boolean look broken. My first run "failed" the subtract test for exactly this reason.

Derived empirically from bounding boxes and volumes; **the package ships no XML documentation**, so
every signature has to be confirmed by experiment. Budget for that.

## Upstream health

MIT, 178 stars, 29 open issues, and the repository was **pushed to on the day of this spike**. It is
genuinely, actively maintained — which makes filing a fillet issue worthwhile, but we cannot make the
schedule depend on someone else's fix.

## Recommendation — amend ADR-0002

Keep CADability. Do not switch kernels wholesale: it is exact where it matters most (booleans,
primitives, topology, STEP/STL), pure managed, MIT, and actively developed. Replacing it over one
feature would cost far more than it saves.

**Route fillet and chamfer to the OCP/build123d sidecar instead**, which ADR-0002 already introduces
for Pipeline A. OCCT's `BRepFilletAPI` is industrial-grade and handles multi-edge and repeated fillets
without complaint. The exchange format is STEP, which the round-trip test above proves works.

Consequences of that split:

- Fillet becomes an **out-of-process, asynchronous** operation. Acceptable: it is a modelling command,
  not a per-frame interaction, and ADR-0001's command bus already makes every edit a discrete,
  undoable, potentially async operation.
- The sidecar becomes a **hard dependency for CAD editing**, not just for generation. That is a real
  cost to weigh — it was previously optional.
- Everything stays open source and inside our own application. No external CAD program is involved.

**If the round-trip latency proves unacceptable in Phase 5**, the fallback is the escape hatch already
named in ADR-0002: a second `ICadKernel` adapter over **Macad.Occt** (MIT, .NET 10, OCCT 7.9.2),
accepting a C++/CLI build in the toolchain.

**Also worth doing:** file an upstream issue with the minimal repro above. The project is active enough
that it may simply get fixed.

## Reproduce

Probe source is in the session scratchpad under `s1/CadProbe`. The findings worth keeping are the
numbers above, the `MakeCylinder` parameter order, and the precise fillet limits.
