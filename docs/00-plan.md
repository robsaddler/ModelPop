# ModelPop — the plan

> Turn an idea, a photo, or a rough prompt into a printable, editable 3D model for a Bambu Lab P2S —
> on your own machine, with your own API keys, using only open-source components.

**Stack: Python, single process** (ADR-0007) - OCCT for CAD, VTK for the viewport, PySide6 for the shell.

## Scope

**In scope.** Text → 3D. Image(s)/video → 3D. Photos with a scale reference → a metric replica.
An in-app 3D viewer. An in-app CAD editor with real modelling operations. Editing by prompt.
Automatic print preparation. Slicing to G-code for the P2S. Sending jobs to the printer.

**Out of scope, deliberately.** A cloud service. A model marketplace. Anything requiring a paid
licence. Multi-user collaboration. Non-FDM processes (resin, metal). Animation, rigging, texturing
for games.

**Hard constraints** (yours, and they shape every decision below):

1. Everything open source. The only spend is AI credits. **GPL/AGPL are fine** - this is not sold.
2. CAD tools live **inside** the app. No shelling out to Fusion or FreeCAD for editing.
3. Bring-your-own keys, configured in an in-app settings panel.
4. SOLID, patterns, modularity and testing are front and centre.

## What makes this different from what already exists

Every consumer AI-3D product today stops at "here is a mesh". The 2026 evidence is unanimous that those
meshes are drafts, not prints: detail baked into textures that vanish when sliced, arbitrary scale, no
flat base, non-manifold geometry. Bambu is retiring its own first-generation AI tools for exactly this
reason.

**ModelPop's thesis: the generator is the easy part; everything after it is the product.**
Repair, scale, wall-thickness enforcement, detail rescue, orientation, readiness scoring, and a real
CAD editor to fix what the AI got wrong. Plus a second pipeline — an LLM writing parametric CAD code in
a measure-and-correct loop — which is the only approach that produces genuinely useful *functional*
parts, and which no consumer product does well.

## The critical path, and why it is ordered this way

The riskiest assumptions get tested first, and a real printed object arrives before any AI is involved.

```
Phase 0  Foundation        ── DONE  domain model, command bus, four gates, CI
Phase 1  See a model       ── DONE  import, viewport, mesh ops, repair, readiness
Phase 2  PRINT something   ── DONE  slicer, auto supports, telemetry   ◄── reached
Phase 4  Generate a part   ── DONE  build123d codegen loop with gates
Phase 6  Edit by prompt    ── DONE  for generated parts: the script is the document

Phase 2b  Verify the G-code ─ unsupported islands, bridges, first-layer area
Phase 2.5 Find something    ─ repository search, gallery, remix a base model
Phase 3   Generate a mesh   ─ generation venv, TRELLIS.2, GPU lease
Phase 5   Edit it properly  ─ sketches, features, feature tree, gizmos
Phase 7   Photos → replica  ─ COLMAP/OpenMVS, ArUco scale
Phase 8   Make it delightful─ detail rescue, hollowing, multi-colour, printer comms
```

**Phases 4 and 6 arrived early, out of order.** Once the CAD kernel was in place, generating a
part and editing one by description were the same loop, and both were reachable without the GPU
work Phase 3 needs. The order in this plan was always about risk, not ceremony: those were the
cheapest remaining paths to something genuinely useful, so they were taken first.

**Phase 2 is the milestone that matters.** At the end of it you can drag a downloaded STL in, see a
readiness report, and print it from ModelPop. That proves the entire back half of the app — file IO,
geometry, 3MF, slicing, the printer — before a single GPU-hour is spent. Most projects like this do it
the other way round, get a shiny demo in week one, and then discover in month three that the output
cannot be printed.

The slicer step, the single biggest external unknown, is **already proven** — see
`docs/research/spike-bambu-cli.md`.

## Phases in detail

### Phase 0 — Foundation *(done)*
Package layout per `01-architecture.md`. `uv`, `pyproject.toml`, `ruff`, `mypy --strict`.
Domain model: `Document`, `Feature`, `Command`, `Mesh`, `Length`/`Unit` value types. The command bus
with undo/redo. `import-linter` contracts that encode the layering. CI on Windows. ADRs 0001–0007.
`CLAUDE.md`.
**Done when:** an empty app starts, the fast test suite runs in under 10 s, and a layering violation
fails the build.

### Phase 1 — See a model *(done)*
STL (binary + ASCII), OBJ and 3MF import. The PySide6 shell and the PyVista/VTK viewport: orbit, pan,
zoom, shaded and wireframe, build-plate and 256 mm print-volume overlay. `MeshOps` with
watertight/manifold checks, repair, decimation, booleans. The hypothesis geometry suite.
**Also here: get VTK onto the RTX 4090 and re-measure** (spike S7 ran on the integrated GPU).
**Done when:** you can open any MakerWorld STL, orbit it, and get an honest verdict on whether it is
manifold.

### Phase 2 — Print something *(done — the milestone)*
Bambu project 3MF writer. `Slicer` over the Bambu CLI. Auto-orientation. G-code verification
(unsupported islands, bridges, first-layer area). The readiness report with traffic lights and
one-click fixes. Fix filament binding, then add the **AMS versus multi-plate** time-and-waste
comparison. Printer gateway over LAN mode, dry-run by default.
**Done when:** a model goes from drag-and-drop to a physical print without leaving ModelPop.
**Result:** reached. Supports are chosen by measuring overhangs rather than always-on, after
discovering that enabling them enlarges the footprint enough to make a 152 mm cube stop fitting.
G-code verification moved to Phase 2b; the rest shipped.

### Phase 2.5 — Find something *(the cheapest useful app there is)*
Repository search across Thingiverse, Printables, Thangs and MyMiniFactory behind the `ModelRepository` port,
with a ranked gallery, licence badges, a one-time acceptance clause, and "use as a base". At this point
ModelPop can find, prepare and print an existing model end to end with **no GPU and no AI credits
spent** — which is a genuinely useful tool already. See `07-discovery-and-remix.md`.
**Done when:** "a dragon about 6 inches tall" returns a usable gallery and one of them prints.

### Phase 3 — Generate a mesh
The generation environment: a separate pinned venv for the PyTorch stack, run as a job with progress,
cancellation and a GPU lease. TRELLIS.2 as the first generator behind the `ModelGenerator` port. Text → image → 3D with the user
approving the reference image. Then straight into the Phase 2 print-prep pipeline. Also the scrubable
virtual print simulator, now that the viewport is solid.
**Done when:** a typed prompt produces a printed object.

### Phase 4 — Generate a part *(done)*
The build123d codegen loop: helper library, dimension table, the six gates, orthographic contact
sheets, numeric failure feedback, best-of-N. The AI settings panel and `SecretStore` land here.
**Done when:** "a wall bracket for a 35 mm pipe with two M4 holes 40 mm apart" prints and fits.
**Result:** the loop is built and covered by tests against a scripted model. It corrects a wrong
dimension from numeric feedback, recovers from a syntax error, keeps the best attempt when nothing
fully passes, and stops at a spend limit. Confirming it against a real model needs an API key.

### Phase 5 — Edit it properly
The in-app CAD editor. Sketching with constraints, extrude/revolve/sweep/loft, booleans, fillets and
chamfers, a feature tree with rebuild, STEP import/export, measurement, section views, gizmos.
Include **text on a surface** (project a string onto a picked face, emboss or deboss by depth) — it is
one of the most-wanted edits on printed models and a natural target for prompt-driven editing.
This is the largest phase; split it into vertical slices, one operation at a time, each fully tested.
**Done when:** you can model a simple mechanical part from scratch without leaving the app.

### Phase 6 — Edit by prompt *(done for generated parts)*
Command schema exposed to the LLM as tools. Validation, clamping and rejection. Preview-then-apply.
Every AI edit is a normal undoable command.
**Done when:** "make the walls 3 mm and add a 2 mm fillet to the top edges" works and is undoable.
**Result:** works for generated parts, where the script is the document and an edit is a rewrite put
through the same gates. Editing an *imported* mesh by description still needs Phase 5's feature
model, and the app says so rather than failing obscurely.

### Phase 7 — Photos → replica
Frame selection, segmentation, COLMAP + OpenMVS, ArUco/ChArUco scale recovery with a printable
calibration mat, plane removal, watertight close-up.
**Done when:** photos of an object on the mat produce a replica that measures correctly with calipers.

### Phase 8 — Delight
Texture→displacement detail rescue. Hollowing with drain holes. Multi-colour part splitting for the
AMS. Generation history and variants. Print monitoring.

## Working method

Build a **walking skeleton first**: in Phase 0–1, wire one trivial path end to end (import → viewport →
readiness → 3MF → slice) with stub implementations, then deepen each stage. This surfaces integration
pain in week one rather than month three.

Each phase: write the ADR, agree the vertical slices, TDD each slice, review, update the skill that
encodes what was learned. Use `superpowers` to enforce the plan-first, test-first loop; write the
domain skills as you go so the knowledge compounds instead of evaporating between sessions.

## Risks, honestly stated

| Risk | Reality | Mitigation |
|---|---|---|
| The in-app CAD kernel is the hardest part of the project | A real parametric kernel is years of work; we are integrating, not writing one | ADR-0007 picks the approach; the `CadKernel` port keeps it swappable; Phase 5 is deliberately last among the core phases |
| ~~The CAD kernel cannot fillet~~ | **Resolved by ADR-0007.** OCCT fillets all 12 edges of a cube in 7 ms (spike S7). |
| 16 GB VRAM constrains generation quality | Real, but adequate — the shape stage of every candidate model fits | One GPU lease; separate processes; hosted fallback behind the same port |
| Licence traps | The obvious best generator excludes UK users; several mesh libraries are GPL/AGPL or non-commercial | ADR-0004 tracks licences explicitly; every dependency is checked before adoption |
| VTK rendered on the integrated GPU | Spike S7 measured 28–36 FPS at 983k triangles on the Intel iGPU, not the 4090 | Force the discrete GPU in Phase 1 and re-measure. We decimate to ~300k for display anyway. |
| Detail rescue (texture→displacement) may not work well | Nobody has solved it; it is genuinely research | Spike it in isolation in Phase 8; the product is valuable without it |
| The generation environment is large | Multi-GB PyTorch/CUDA install | It is a separate, optional venv; the app is fully useful without it (Phases 2 and 2.5) |
| Scope | This is a big build | The phase order guarantees something useful and printable from Phase 2 onward |

## Documents

| File | What it holds |
|---|---|
| `01-architecture.md` | Layering, ports, patterns, SOLID, concurrency, security |
| `02-tech-stack.md` | Every library, with its licence |
| `03-pipelines.md` | The router, the three generation pipelines, print prep, evaluation |
| `06-phase-0-checklist.md` | Exactly what to do first |
| `07-discovery-and-remix.md` | Repository search, the gallery, the licensing stance |
| `08-gcode-verification.md` | Will it actually print — islands, bridges, collisions |
| `09-virtual-print.md` | The virtual printer, and AMS vs multi-plate waste and time |
| `04-engineering-standards.md` | Definition of done, test strategy, tooling, CI |
| `05-skills-plan.md` | Which Claude skills to install, and which to write |
| `research/spike-bambu-cli.md` | Verified slicer facts, measured on this machine |
| `research/spike-s7-python-stack.md` | **Why the stack is Python** — measured, not argued |
| `research/findings.md` | The 2026 landscape digest with citations |
| `research/spike-s7-python-stack.md` | **Why the stack is Python** - measured, not argued |
| `adr/` | Decision records |
