# ModelPop — Architecture

## The one idea that holds the whole app together

**Everything that changes a model is a `Command` in an append-only document history.**

Three different producers emit the same commands:

```
   User clicks "Fillet 2mm"  ─┐
   LLM: "round the corners"  ─┼─►  Command  ─► CommandBus ─► Document (feature list) ─► Rebuild ─► Mesh
   Script / replay / test    ─┘                    │
                                                   └─► Undo / Redo / Serialise / Diff
```

Why this is the load-bearing decision:

- **Prompt-driven editing becomes safe.** The LLM never writes arbitrary code into your process. It
  emits a JSON array of typed, schema-validated commands. Anything it cannot express, it cannot do.
- **Undo/redo is free**, for AI edits exactly as for manual ones. An AI edit that goes wrong is one
  Ctrl+Z, which is the difference between a toy and a tool.
- **Parametric rebuild is free.** The document *is* the feature history; rebuild is a replay.
- **Testing is trivial.** A test is "apply these commands, assert these invariants". No UI, no mocks.
- **The AI is testable offline.** Record prompt → command-array pairs as fixtures; assert the model
  produces valid, sane commands without ever calling a network.

Reject any design that lets a generated artefact mutate geometry outside this bus.

---

## Layering (Hexagonal / Ports & Adapters)

Dependencies point **inwards only**. Enforced by `import-linter` contracts in CI, not by goodwill.

```
┌───────────────────────────────────────────────────────────────────────┐
│  modelpop.ui               PySide6 shell, views                        │
│  modelpop.presentation     view-models — NO UI framework imports       │
│  modelpop.rendering        PyVista/VTK viewport, picking, gizmos       │
├───────────────────────────────────────────────────────────────────────┤
│  ADAPTERS (implement the ports)                                        │
│  cad_occt (build123d)   mesh_trimesh   ai_anthropic   ai_ollama        │
│  gen_trellis   printing_bambu   vision_opencv   repositories           │
├───────────────────────────────────────────────────────────────────────┤
│  modelpop.application      use cases, pipelines, PORT PROTOCOLS        │
│                            (CadKernel, MeshOps, ModelGenerator,        │
│                             Slicer, AiProvider, PrinterGateway…)       │
├───────────────────────────────────────────────────────────────────────┤
│  modelpop.domain           Document, Feature, Command, Mesh, Units,    │
│                            print-readiness rules. NO dependencies.     │
└───────────────────────────────────────────────────────────────────────┘
```

`modelpop.domain` imports **nothing** — not a mesh library, not a kernel, not an HTTP client.
Mesh data in the domain is a plain, immutable value type (vertices + indices + a unit). Every heavy
operation is behind a port. This is what makes the kernel decision reversible; see ADR-0007.

### Project layout

```
src/modelpop/
  domain/            pure: Document, Feature, Command, Mesh, Length/Unit. No deps.
  application/       use cases, pipelines, port Protocols
  cad/               build123d / OCCT adapter
  mesh/              trimesh, manifold3d, pymeshfix, PyMeshLab adapters
  ai/                anthropic / openai-compatible / ollama adapters
  generation/        the four pipelines + intent router
  vision/            ArUco scale, segmentation, frame selection
  printing/          3MF writer, slicer adapter, printer gateway
  repositories/      model search adapters (Thingiverse, …)
  rendering/         PyVista/VTK viewport
  presentation/      view-models — imports no UI framework
  ui/                PySide6 shell and views
tests/
  domain/            fast, pure
  geometry/          hypothesis property tests + golden meshes
  architecture/      import-linter contracts
  integration/       slicer, file IO   @pytest.mark.integration
  evals/             scored prompt suites (opt-in, costs credits)
```

---

## The thirteen ports

Every external capability is one interface. Nothing else in the app knows the implementation exists.

| Port | Responsibility | First adapter | Later adapters |
|---|---|---|---|
| `CadKernel` | sketch, extrude, revolve, boolean, fillet, STEP IO, tessellate | build123d / OCCT (ADR-0007) | — |
| `MeshOps` | boolean, repair, remesh, decimate, offset/hollow, watertight check | trimesh + manifold3d + pymeshfix | PyMeshLab, Open3D, bpy |
| `ModelRepository` | search existing models, fetch one with its licence | Thingiverse | Printables, Thangs, MyMiniFactory |
| `ModelGenerator` | prompt/image → mesh | TRELLIS.2 | TripoSG, hosted APIs |
| `Reconstructor` | photos/video → scaled mesh | COLMAP + OpenMVS | feed-forward models |
| `ScaleEstimator` | pixels → millimetres | ArUco via OpenCV | known-object, metric depth |
| `AiProvider` | chat, vision, tool-calling, streaming | Anthropic | OpenAI-compatible, Ollama |
| `Slicer` | oriented 3MF → gcode + telemetry | Bambu Studio CLI **(proven)** | OrcaSlicer, PrusaSlicer |
| `GcodeVerifier` | toolpath → what will go wrong when it runs | Bambu dialect reader **(proven)** | other slicer dialects |
| `PartGenerator` | description → parametric part, and edits to one | build123d codegen loop **(proven)** | other kernels, other loops |
| `PrinterGateway` | send job, query status | Bambu LAN mode (MQTT/FTPS) | — |
| `PrinterProfile` | machine, toolhead/gantry geometry, AMS units and loaded filaments | P2S + AMS | other Bambu models |
| `Viewport` | render, pick, gizmo, camera | PyVista / VTK (ADR-0007) | — |

**Rule:** a port is defined by what the *application* needs, never by what the library offers. If
`CadKernel` starts leaking OCCT types, the abstraction has failed and the kernel is no longer swappable.

---

## Patterns, and where each one earns its place

Patterns are used where they remove a real problem. Each entry below names the problem first.

| Problem | Pattern | Where |
|---|---|---|
| AI edits must be reversible and validated | **Command** + **Memento** | `Command`, `CommandBus`, `DocumentHistory` |
| Print prep is a long, ordered, fallible sequence that must be inspectable | **Pipeline** (chain of stages) | `PipelineStage` protocol — repair, scale, thickness, orient, hollow, decimate |
| Several generators, chosen at runtime by intent and availability | **Strategy** + **Factory** | `ModelGenerator`, `GeneratorSelector` |
| A request may be mechanical, organic or a replica | **Router / Strategy** | `IntentRouter` picks the pipeline (see `03-pipelines.md`) |
| Printability is many independent rules that must be reported, not just thrown | **Specification** | `PrintRule` → `ReadinessReport` |
| Heavy ops must not block the UI and must be cancellable | **Mediator over a job queue** | `JobScheduler`, everything returns a `JobHandle` |
| Failures are expected, not exceptional (bad mesh, model refusal, slicer warning) | **Result-style returns** | no control-flow exceptions across a port boundary |
| Only one 3D model fits in 16 GB VRAM at a time | **Object pool / lease** | `GpuLease` — a worker must hold the lease to load weights |
| Config, keys, model choice | **Options pattern** | a reloadable settings object so the settings panel applies live |

**Anti-goals.** No service locator. No module-level mutable geometry state. No `mesh_utils` god-module. No
inheritance where composition works. Abstractions are introduced when there is a *second* implementation
or a *test* that needs one — not speculatively.

---

## SOLID, concretely

- **S** — `PrintPrepPipeline` orchestrates; it does not repair meshes. Each stage does one transform and
  is independently testable. If a class name contains "Manager" or "Helper", it is a smell to review.
- **O** — adding a new generator or slicer means adding a class and one wiring entry. It must require
  **zero** edits to `application`. This is directly testable: the architecture contract asserts no
  `Application` type references a concrete adapter.
- **L** — every `MeshOps` implementation must satisfy the same property-based contract suite
  (`MeshOpsContractTests<T>`), so substituting Manifold for PicoGK cannot silently change behaviour.
- **I** — `AiProvider` is split: `ChatProvider`, `VisionProvider`, `ToolCallingProvider`. Ollama
  implements the first two and not the third, honestly, rather than raising `NotImplementedError`.
- **D** — `Domain` and `Application` depend only on abstractions; composition happens once, at
  application start-up, in a single wiring module.

**One extra rule.** `modelpop.presentation` holds the view-models and must
reference **no** UI framework — no PySide6, no Qt. Views bind to it; headless tests drive it. This
is what makes a WinUI head additive rather than a rewrite, and it is enforced by an `import-linter` contract.

---

## Concurrency and the GPU

- The Qt event loop does nothing but UI. All generation, slicing and meshing run as jobs on a bounded
  scheduler, off the UI thread.
- **One GPU lease.** 16 GB VRAM holds exactly one large generative model. `GpuLease` serialises access;
  a worker that cannot take the lease queues. Workers are separate processes so VRAM is reclaimed on exit.
- Long jobs report progress and are cancellable end to end, including into the generation subprocess
  and into the slicer (kill the process).
- Meshes are immutable; a pipeline stage returns a new mesh. This kills a whole class of threading bugs
  and makes every stage cacheable by content hash.

## Caching

Content-address everything expensive. Key = SHA-256 of (inputs + stage parameters + adapter version).
Generation, reconstruction and slicing all write into a local cache under `%LOCALAPPDATA%\ModelPop\cache`.
Re-running a pipeline after changing only the last stage must not re-run the GPU work.

## Security and trust boundaries

- API keys go in the OS credential store (`keyring`), never in
  a config file, never in logs. The settings panel writes through `SecretStore`.
- **Treat model output as untrusted data.** The LLM produces commands that are schema-validated and
  range-checked before reaching the bus. Generated CAD code runs **only** in a restricted subprocess,
  with no network, a working directory confined to a temp folder, and a wall-clock timeout.
- Downloaded model weights are checked against a pinned hash before use.
