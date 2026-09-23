# ModelPop

Turn an idea into a **printable, editable** 3D model for a Bambu Lab P2S — locally, with your own
API key, using only open-source components.

A Windows desktop app (Python, PySide6 + VTK, OCCT for real CAD) that combines AI part generation
with print-readiness checking and slicing.

## Why

Every consumer AI-3D tool stops at "here is a mesh". Those meshes are drafts, not prints: detail
baked into textures that vanishes when sliced, arbitrary scale, no flat base, non-manifold geometry.
Bambu retired its own first-generation AI tools in September 2026 for exactly this reason.

**ModelPop's thesis: the generator is the easy part; everything after it is the product.**

## What works today

| | |
|---|---|
| **Open a model** | STL, OBJ, 3MF, PLY, GLB, OFF. Assessed for printability the moment it loads. |
| **See it** | VTK viewport with the P2S build volume drawn to scale, orbit/pan/zoom, section views. |
| **Know if it will print** | Watertight, wall thickness, overhangs, tip-over risk, build-volume fit, triangle budget. Every finding names a remedy. |
| **Repair it** | Escalates from cleanup through hole filling to a voxel rebuild. Reports failure rather than returning a broken mesh. |
| **Scale it** | "About six inches tall" is a first-class operation. Units are a type, not a float. |
| **Slice it** | Bambu Studio CLI, with supports chosen by measuring the geometry. Returns predicted time and warnings. |
| **Generate a part** | Describe a mechanical part; a model writes build123d code, it runs sandboxed, and the solid is **measured against your dimensions** and corrected until it matches. |
| **Build it yourself** | CAD tools in the app: box, cylinder, sphere, fillet, chamfer, hollow, holes and pockets, move, rotate, scale to a size, text on a face. A feature tree, and undo. |
| **Change it by saying so** | "Round the corners and hollow it out." The model replies with the **same typed commands the toolbar emits**, so an AI edit joins the tree and undoes like anything else. |
| **Save the project** | The feature tree as readable JSON. No geometry - the shape is rebuilt, so a saved model picks up later improvements to how an operation is built. |
| **Find something to start from** | Search MyMiniFactory and Thingiverse at once, ranked with reasons, with a licence badge on every card. |
| **Check the toolpath** | Reads the sliced G-code back and finds material starting in mid-air, plus tip-over risk and by-object collisions. |
| **Watch it print** | Scrub through the print, coloured by the slicer's own feature names, with the nozzle where it will be. |
| **Weigh the AMS against it** | One plate with filament swaps, or one plate per colour. Measured from the slicer's own purge volumes and times, not modelled. |
| **Make one from a picture** | A photo or a drawing into a printable mesh, on your own GPU. 36 seconds for a draft on a 4090. |

### Not yet built

Multi-photo photogrammetry. Sketches and gizmos. Multi-colour part splitting - the AMS comparison
works on any multi-colour slice you bring it, but ModelPop cannot yet split a model into colours
itself. Text straight to a mesh: the generator is image-to-3D and refuses rather than making you
something you did not ask for. See `docs/00-plan.md`.

## Running it

Requires **Python 3.13 or newer** (3.14 is what it is developed on) and, for slicing, **Bambu Studio**. Keep the checkout at a short path —
long Windows paths break `pip`.

```bash
uv venv
uv pip install -e . --group dev
uv run modelpop
```

Generating parts needs an Anthropic API key. Add it in **File → Settings**; it goes to the OS
credential store, never to a file in this repo. An exported `ANTHROPIC_API_KEY` takes precedence.

Everything except generation works without a key, and everything except slicing works without
Bambu Studio.

Searching for models needs a free key from MyMiniFactory or Thingiverse, also in **File →
Settings**. Making a model from a picture needs a CUDA graphics card and a one-off download - see
[`docs/10-mesh-generation.md`](docs/10-mesh-generation.md). Each of these is optional, and each says
specifically which piece is missing rather than failing at the click.

## Developing

```bash
uv run pytest -m "not integration"   # the fast suite, under 10 seconds
uv run pytest                        # everything, including the real slicer and CAD kernel
uv run ruff check . && uv run mypy && uv run lint-imports
```

The four gates are non-negotiable: `ruff`, `mypy --strict`, `import-linter`, and the tests.
`import-linter` is what keeps the layering honest — the domain imports no kernel, the application
imports no adapter, and the view-models import no UI framework, which is why the whole user journey
can be tested with no display.

## Documents

| File | What it holds |
|---|---|
| [`docs/00-plan.md`](docs/00-plan.md) | Scope, the phases, risks. **Read this first.** |
| [`docs/01-architecture.md`](docs/01-architecture.md) | Layering, the ports, patterns, SOLID, security |
| [`docs/02-tech-stack.md`](docs/02-tech-stack.md) | Every library, with licence |
| [`docs/03-pipelines.md`](docs/03-pipelines.md) | The router, generation pipelines, print prep |
| [`docs/04-engineering-standards.md`](docs/04-engineering-standards.md) | Definition of done, test strategy, CI |
| [`docs/05-skills-plan.md`](docs/05-skills-plan.md) | Claude Code skills to install, and to write |
| [`docs/08-gcode-verification.md`](docs/08-gcode-verification.md) | Will it actually print - and what real Bambu G-code taught us |
| [`docs/09-virtual-print.md`](docs/09-virtual-print.md) | The print preview, and where its clock comes from |
| [`docs/10-mesh-generation.md`](docs/10-mesh-generation.md) | Turning a picture into a mesh, and why it needs its own Python |
| [`docs/adr/`](docs/adr/) | Decision records, including the three that were superseded or refined |
| [`docs/research/`](docs/research/) | Measured spike results, and what they cost to learn |

## Constraints

1. Open source only — the sole running cost is AI API credits. GPL and AGPL are fine; this is not sold.
2. CAD editing happens **inside** the app. Everything that changes a parametric model is a typed
   command on one bus - whether a toolbar, a prompt or a replay asked for it (ADR-0001, ADR-0009).
3. Bring your own AI key, configured in-app.
4. SOLID, patterns, modularity and testing are the point.
