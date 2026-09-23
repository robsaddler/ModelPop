# ModelPop

Turn an idea, a photo, or a rough prompt into a **printable, editable** 3D model for a Bambu Lab P2S —
locally, with your own API keys, using only open-source components.

A Windows desktop app (**Python**, PySide6 + VTK, with OCCT for real CAD) that combines AI model
generation with an in-app parametric CAD editor and an automatic print-preparation pipeline.

## Why

Every consumer AI-3D tool stops at "here is a mesh". Those meshes are drafts, not prints: detail baked
into textures that vanishes when sliced, arbitrary scale, no flat base, non-manifold geometry. Bambu
retired its own first-generation AI tools in September 2026 for exactly this reason.

**ModelPop's thesis: the generator is the easy part; everything after it is the product.**

## Status

Planning complete; implementation not started. Three assumptions are already **proven on the target
machine**: slicing through the Bambu Studio CLI, the OCCT CAD kernel (fillets all 12 edges of a cube
in 7 ms), and the VTK viewport (983k triangles, picking in 0.0045 ms). See `docs/research/`.

## Start here

| Document | What it holds |
|---|---|
| [`docs/00-plan.md`](docs/00-plan.md) | Scope, the eight phases, risks. **Read this first.** |
| [`docs/01-architecture.md`](docs/01-architecture.md) | Layering, the nine ports, patterns, SOLID, concurrency, security |
| [`docs/02-tech-stack.md`](docs/02-tech-stack.md) | Every library, with licence |
| [`docs/03-pipelines.md`](docs/03-pipelines.md) | The router, three generation pipelines, print prep, evaluation |
| [`docs/04-engineering-standards.md`](docs/04-engineering-standards.md) | Definition of done, test strategy, CI |
| [`docs/05-skills-plan.md`](docs/05-skills-plan.md) | Claude Code skills to install, and the ones to write |
| [`docs/adr/`](docs/adr/) | Decision records |
| [`docs/research/`](docs/research/) | The 2026 landscape digest, and the verified slicer spike |

## Constraints

1. Open source only — the sole running cost is AI API credits. GPL/AGPL are fine; this is not sold.
2. CAD editing happens **inside** the app.
3. Bring your own AI keys, configured in-app.
4. SOLID, patterns, modularity and testing are the point.
