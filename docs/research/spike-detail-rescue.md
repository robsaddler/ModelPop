# Spike — detail rescue, texture into geometry — WORKS, and the hard part is not the hard part

Run on this machine, 2026-09-24. This is the Phase 8 item the plan has called *"genuinely
research"* since day one: a generated model carries its fine detail in a colour texture, and colour
is invisible to a slicer, so the print comes out a smooth blob.

The measurement says the **mechanics are not the problem**. Baking luminance into the surface is
exact, fast and safe. What is left is a question of taste, not of feasibility.

## What was measured

A UV sphere standing in for a generated model, a texture with features at a known spatial
frequency, and a displacement of each vertex along its own normal by the luminance under it.

### The displacement lands exactly where it is asked to

| Vertices | Time | Asked | Got (max) | Watertight | Winding |
|---|---|---|---|---|---|
| 642 | 0.9 ms | 0.60 mm | 0.600 mm | yes | consistent |
| 2,562 | 1.7 ms | 0.60 mm | 0.600 mm | yes | consistent |
| 10,242 | 6.2 ms | 0.60 mm | 0.600 mm | yes | consistent |

Six milliseconds at ten thousand vertices. This was expected to be the expensive part and it is
not.

### It does not tear the model

A texture wraps, and the seam where `u` returns to zero is the obvious place for a mesh to split.
It does not: the displaced sphere is still watertight, still consistently wound, and its **Euler
number is unchanged** — the topology is exactly what it was.

The centring is what makes that true. The luminance is centred on its own mean before it is
applied, so the surface moves in and out about where it was rather than swelling outwards. A bake
that only ever pushes outward inflates the model, and the size the user asked for quietly stops
being the size they get.

### The binding constraint is mesh density, not the printer

A feature needs about two vertices across it to exist in geometry at all:

| Vertices (60 mm sphere) | Mean edge | Smallest feature it can hold |
|---|---|---|
| 642 | 4.52 mm | ~9.0 mm |
| 2,562 | 2.27 mm | ~4.5 mm |
| 10,242 | 1.13 mm | ~2.3 mm |
| 40,962 | 0.57 mm | ~1.1 mm |

Against a printer floor of **0.42 mm** across (one extrusion line at a 0.4 mm nozzle) and **0.2 mm**
deep (one layer), even forty thousand vertices is still the limit — by a factor of nearly three.

So the mesh has to be subdivided before it is displaced, or the detail has nowhere to live. And
that is cheap too: **642 vertices to 10,242 in 2 ms**, with the displacement still exact and the
result still watertight.

## What this means

1. **Build it.** The operation is exact, fast, and topology-safe. The plan's worry was that this
   was research; the mechanics are not.
2. **Subdivide first, to a target driven by the feature size asked for** — not to a fixed count.
   The number that matters is the mean edge length against the smallest feature wanted.
3. **Clamp the amplitude to something a printer can show.** Below one layer height the bake changes
   the mesh and nothing else: the slice comes out identical and the user concludes the feature is
   broken.
4. **Say that luminance is a guess.** It is not a height map. A dark patch might be a groove or it
   might be a dark patch, and no amount of engineering makes that distinction. That is the part
   that is genuinely unsolved, and the honest thing is to let the user set the depth, see the
   result, and undo it — not to pretend the number means something it does not.

## What was not measured

Whether the rescued detail **looks right** on a real generated model. That needs the generation
environment and a real textured GLB, and it is a judgement rather than a number. The bake is
reversible and the amplitude is the user's to set, which is the right shape for something nobody
can predict.
