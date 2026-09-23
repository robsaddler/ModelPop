# Phase 0 — foundation checklist

Concrete, ordered, finishable. Nothing here is speculative; every item exists to make a later phase
cheaper or to prove an assumption before it becomes expensive.

**Stack is Python** (ADR-0007). The three architecture-critical spikes are done.

## 0.1 Install the skills (30 minutes, do this first)

```
/plugin install superpowers@claude-plugins-official
/plugin install skill-creator@claude-plugins-official
/plugin install mattpocock-skills@claude-plugins-official
/plugin install feature-dev@claude-plugins-official
/plugin install pr-review-toolkit@claude-plugins-official
/plugin install pyright-lsp@claude-plugins-official
/plugin install mcp-server-dev@claude-plugins-official
/plugin install security-guidance@claude-plugins-official
/plugin install commit-commands@claude-plugins-official
/plugin install github@claude-plugins-official

/plugin marketplace add anthropics/skills
/plugin install claude-api@anthropic-agent-skills
```

Note `pyright-lsp` replaces `csharp-lsp` now the stack is Python. Rationale in `05-skills-plan.md`.
Do `superpowers` and `skill-creator` even if you skip the rest.

## 0.2 Spikes

Each is a throwaway script. Timebox each to an afternoon. **Write up the result in `docs/research/`
whether it succeeds or fails** — a documented failure has done its job.

| # | Spike | Status |
|---|---|---|
| **S7** | **Python stack — build123d/OCCT + PyVista/VTK** | **DONE — PASSED.** 12/12 CAD tests. Fillet all 12 edges of a cube in **7 ms**; booleans exact; native 3MF. Viewport: 983k triangles, picking in **0.0045 ms**. `research/spike-s7-python-stack.md` |
| **S1** | ~~CADability (C#)~~ | **DONE — superseded.** Booleans exact but **fillet unusable**: one edge once, 2+ returns null, second pass throws. Drove ADR-0007. `research/spike-s1-cadability.md` |
| **S3** | ~~Avalonia + HelixToolkit (C#)~~ | **DONE — superseded.** Passed at 1,015,808 tri / 54.9 FPS on Avalonia 11.3.22; Avalonia 12 breaks it. `research/spike-s3-avalonia-helix.md` |
| S2 | **Mesh stack** — trimesh + manifold3d boolean; `pymeshfix` repair of a deliberately broken mesh; watertight assertion | repairs non-manifold input to watertight |
| S4 | **Slicer CLI from Python** — reproduce the shell spike with `subprocess.run([...])` | `return_code: 0`, telemetry parsed |
| S5 | **GPU generation env** — separate uv venv with PyTorch, load TRELLIS.2, measure peak VRAM at 512³ and 1024³ | fits in 16 GB, cancellable |
| S6 | **Anthropic Python SDK** — a vision call with an image, and a tool-call round trip | both work, key read from `keyring` |

**Also still open:** get VTK onto the **RTX 4090** and re-measure. Spike S7 ran on the Intel iGPU
(28–36 FPS at 983k triangles), so that figure is a floor, not a ceiling. Not architecture-changing.

## 0.3 Project scaffolding

- `uv init`; package layout per `01-architecture.md` (`src/modelpop/...`).
- `pyproject.toml` is the single source of project, dependency and tool config. Commit `uv.lock`.
- `ruff` and `mypy --strict` configured and clean from the first commit.
- `.importlinter` contracts written now, while there is nothing to fix.
- **Keep the repo and venv at a short path.** Long Windows paths break `pip` (spike S7).

## 0.4 The domain model

Small, immutable, dependency-free. Resist the urge to make it clever.

- `Length` / `Unit` value types so millimetres can never be confused with inches. Scale is the most
  common complaint about AI-generated models; make the mistake unrepresentable.
- `Mesh` — frozen dataclass: vertices, indices, unit. No library types.
- `Document` — ordered feature list plus a content hash.
- `Command`, `CommandBus`, `DocumentHistory` with undo/redo.
- A `Result` type for expected failures.
- `PrintRule` and `ReadinessReport` shells.

## 0.5 The test harness

- `pytest` per layer; fast suite under 10 seconds; `@pytest.mark.integration` for the rest.
- **hypothesis** wired up with the first property test: `undo(apply(cmd))` restores the document hash.
- **`import-linter` contracts written now**, while there is nothing to fix:
  - `modelpop.domain` imports nothing outside itself and the standard library
  - `modelpop.application` imports no adapter module and no third-party geometry type
  - `modelpop.presentation` imports no UI framework
- `syrupy` configured with fixed 6 dp rounding for geometry snapshots.

## 0.6 CI

GitHub Actions on `windows-latest`: `uv sync`, `ruff check`, `mypy`, fast tests, `lint-imports`,
coverage. Nightly: `mutmut` and the eval suite. Pre-commit hook for format plus fast tests.

## 0.7 Walking skeleton

Wire one trivial path end to end with stubs: **import an STL → show it in the viewport → produce a
readiness report → write a 3MF → slice it**. Every stage may be a stub that does almost nothing. The
point is that the seams exist and are exercised by an integration test on day one, so integration pain
arrives in week one rather than month three.

## 0.8 Documentation

- `CLAUDE.md` — done.
- ADRs 0001–0007 — done. 0007 supersedes 0002 and 0003 on measured evidence.
- Write the `modelpop-conventions` skill with `skill-creator`.
- Optional courtesy: file the CADability fillet repro upstream. We no longer depend on it.

## Definition of done for Phase 0

An empty ModelPop window opens. `pytest -m "not integration"` runs in under 10 seconds. A deliberate
layering violation fails `lint-imports`. The walking skeleton slices a cube. Every spike is written up.
