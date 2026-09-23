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
| **Change a part** | "Make the walls 3 mm." The script is rewritten and re-checked through the same gates. |

### Not yet built

Photos to a scaled replica, organic mesh generation (TRELLIS.2), repository search, interactive
sketch-based CAD editing, G-code verification, the virtual printer. See `docs/00-plan.md`.

## Running it

Requires **Python 3.13+** and, for slicing, **Bambu Studio**. Keep the checkout at a short path —
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
| [`docs/adr/`](docs/adr/) | Decision records, including the two that were superseded |
| [`docs/research/`](docs/research/) | Measured spike results, and what they cost to learn |

## Constraints

1. Open source only — the sole running cost is AI API credits. GPL and AGPL are fine; this is not sold.
2. CAD editing happens **inside** the app.
3. Bring your own AI key, configured in-app.
4. SOLID, patterns, modularity and testing are the point.
