# Virtual printing — simulate the print before committing filament

Requested feature. Assessed honestly below: the simulator is straightforward, the collision detection
is narrower than it sounds, and the **AMS-versus-multi-plate comparison is the genuinely valuable
part** — and it turns out to be directly computable from data the slicer already hands us.

## 1. The simulator

We already parse the G-code for stage P13, so animating it is mostly presentation.

- Configure a **virtual printer**: model (P2S), nozzle diameter, build volume, plus **zero or more AMS
  units** each with four slots, and a filament (material + colour) assigned per slot.
- Replay the toolpath with a time-accurate scrub: play, pause, step by layer, jump to a timestamp.
- Draw material as it is laid down, coloured by the active filament, with the toolhead and gantry shown
  as real geometry rather than a dot.
- Colour by feature type using the slicer's own categories so the simulation and the telemetry agree.

It reuses the viewport and the G-code parser, so the marginal cost is modest. And watching the thing
build is genuinely enjoyable, which matters for a tool you use for fun.

## 2. What it can honestly detect

| Check | Detectable? |
|---|---|
| **Sequential / by-object print collisions** | **Yes** — the real collision case. Sweep the toolhead and gantry volume along the path against already-completed objects. |
| **Gantry clearance over tall objects** | **Yes** — needs the toolhead and gantry dimensions in the printer profile. |
| **Toolpath outside the build volume** | Yes, trivially. |
| **Purge tower / wipe tower conflicts** | Yes — it is just another object in the scene. |
| **Nozzle hitting the object currently being printed** | **No, and it cannot happen.** Layer-by-layer building means the nozzle is always at the top of what exists. |
| **Curled or warped parts lifting into the nozzle** | **No.** This is a thermal and material effect, not a geometric one. No geometric simulator predicts it. |

So the simulator's collision value is real but specific: it is about **multiple objects**, not about a
single model. Worth building, worth not overselling.

## 3. AMS versus multi-plate — the feature worth building first

This is a real decision every multi-colour print forces, and today people guess at it. We can answer it
with numbers, because the slicer reports exactly what we need.

**The trade-off.** One plate with an AMS means a tool change every time the colour changes, often
several times per layer. Each change purges filament — the "poop" — and costs time. Splitting the model
by colour across separate single-filament plates eliminates purging entirely but costs you plate
changes, more wall-clock time overall, and manual assembly.

**How we compute it.** Slice the same model both ways through the CLI we already drive, then compare.
`result.json` gives us, per plate:

- `filament_change_times` — how many tool changes
- `filaments[].total_used_g` and `main_used_g` — **`total_used_g` minus `main_used_g` is the purge
  waste**, which is exactly the number the user wants
- `total_predication` — predicted print time
- `layer_filament_change` — changes per layer

Present it as a straight comparison:

| | Single plate, AMS | Split across plates |
|---|---|---|
| Print time | from `total_predication` | sum across plates |
| Filament used | sum of `main_used_g` | same |
| **Purged waste** | sum of (`total_used_g` − `main_used_g`) | ~0 |
| Tool changes | `filament_change_times` | 0 |
| Manual effort | one plate, done | N plate changes + assembly |

Then let the user decide, with the actual grams and minutes in front of them. Add a "what if" control
for **flush volume**, since purge cost depends heavily on the colour pair — going from black to white
purges far more than white to grey — and that is a setting people rarely realise they can tune.

**Prerequisite.** Our slicer spike came back with `filament_id` empty and `main_used_g` at `0`, so
filament binding must be fixed first (tracked in ADR-0006 and the spike write-up). Without it there are
no grams, and without grams there is no comparison. Fix it in Phase 2.

## 4. Where this lands

- **Phase 2:** fix filament binding; add the AMS-versus-multi-plate comparison. It is nearly free once
  the slicer adapter exists, and it is immediately useful.
- **Phase 3:** the scrubable simulator with material accumulation, built on the viewport.
- **Later:** sequential-print collision sweeping, once multi-object plates are supported.

New port: `IPrinterProfile` describing the machine, its toolhead and gantry geometry, and the attached
AMS units with their loaded filaments. The virtual printer and the real one share it, so what you
simulate is what you print.
