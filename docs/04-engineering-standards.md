# ModelPop — Engineering standards

Non-negotiables. These exist because a geometry app fails in ways that are silent: a mesh that is
subtly non-manifold slices fine until it doesn't, and a regression in a boolean shows up as a print
failure three days later. Tests are the only defence.

## Definition of done

A change is done when **all** of these are true:

1. It has tests that would fail without it.
2. `ruff check` and `mypy --strict` are clean.
3. `import-linter` contracts pass (no layering violation).
4. Coverage on new code ≥ 80% lines, and every public `domain`/`application` symbol is exercised.
5. Public API has docstrings; anything non-obvious has a *why* comment, never a *what* comment.
6. The ADR is written if an architectural choice was made or reversed.

## Test strategy, by layer

| Layer | Style | Runs in |
|---|---|---|
| Domain | Pure unit tests. Fast, no IO, no clock, no randomness. | every save |
| Geometry | **hypothesis** property tests + golden-file snapshots | every save |
| Application | Use-case tests with fake ports | every save |
| Adapters | Contract tests + integration tests against the real dependency | pre-push |
| AI | Record/replay fixtures for determinism; scored evals opt-in | pre-push / manual |
| UI | View-model tests (no UI framework needed) + `pytest-qt` for widget interaction | every save |

**The whole fast suite must run in under 10 seconds.** If it doesn't, the suite stops being run.
Integration tests are marked `@pytest.mark.integration` and excluded from the fast loop.

### Property-based testing is the highest-value technique here

Geometry has invariants that example-based tests barely probe. Assert properties over generated inputs:

- the boolean of two watertight solids is watertight
- `union(a,b)` volume ≤ `volume(a) + volume(b)`, and ≥ `max(volume(a), volume(b))`
- `union(a,a) ≡ a` (idempotence); `union(a,b) ≡ union(b,a)` (commutativity)
- decimating to 90% then to 80% ≈ decimating to 80% within a silhouette tolerance
- any repaired mesh is manifold, or repair reported failure — never "succeeded" while broken
- scaling by `s` then by `1/s` returns the original within floating-point tolerance
- every command has an inverse: `apply(cmd)` then `undo(cmd)` restores the document hash

Shrinking will hand you the minimal degenerate mesh that breaks the kernel, which is worth more than
any number of hand-written cases.

### Golden-file / snapshot testing

Use `syrupy` for meshes and renders, with two rules that matter:

- **Serialise geometry with fixed rounding** (6 dp, `repr` stable across platforms) so
  snapshots are stable across machines and floating-point drift.
- **Compare renders with a tolerance**, not byte-exactly, and mark them unique per OS/GPU. A byte-exact
  image assertion on a GPU render is a test that fails for the wrong reasons forever.

Never commit a snapshot you have not looked at. An accepted-without-reading snapshot is worse than no test.

### Architecture tests (`import-linter`)

Encode the layering as contracts in `.importlinter`. At minimum:

- `modelpop.domain` imports nothing outside itself and the standard library.
- `modelpop.application` imports no adapter module and no third-party geometry type.
- `modelpop.presentation` imports no UI framework (no PySide6, no Qt).
- Only the wiring module constructs adapters.
- Domain values are frozen dataclasses or immutable collections.
- Nothing outside `modelpop.ai` imports an AI SDK.

These are the rules that actually keep the kernel swappable. Without them, layering decays in weeks.

### Mutation testing

Run `mutmut` weekly, not per-commit, and scope it to `domain` and `application`. Exclude
geometry adapters — the run time is not worth it. A surviving mutant in the readiness rules or the
command system is a real bug waiting to happen; treat it as a defect.

### Benchmarks

Benchmark the operations that will decide whether the app feels fast: boolean on a 500 k-triangle mesh,
decimation, SDF thickness sampling, STL/3MF read and write, mesh upload to the GPU. Record results in
`docs/benchmarks/` so regressions are visible. Do not optimise before measuring.

## Testing the AI without burning credits

This is where most AI projects have no discipline. Three tiers:

1. **Unit** — the provider adapter is tested against recorded HTTP fixtures (`vcrpy` or hand-rolled). Deterministic, free,
   runs in the fast suite. Covers retry, streaming, tool-call parsing, error mapping.
2. **Contract** — the command schema (pydantic models). Given a recorded model response, assert we validate, clamp and
   reject correctly. Include deliberately malicious responses: out-of-range values, unknown command
   names, nested injection attempts in a filename. **These are security tests.**
3. **Evals** — a fixed suite of ~30 prompts with expected outcomes, scored by the gates in
   `03-pipelines.md`. Opt-in (`pytest -m eval`), costs credits, tracked over time
   in `docs/evals/`. This is how you know whether a prompt change helped or hurt, instead of guessing.

Never let an eval failure block the build. Do let it block a release.

## Code style and tooling

- Full type hints; `mypy --strict`; `ruff` for lint and format, configured in `pyproject.toml`.
- **uv** for environments and a committed lockfile, so versions are pinned and reproducible.
- `pyproject.toml` is the single source of project, tool and dependency configuration.
- Lint findings are errors in CI. If a rule is genuinely wrong, disable it in `pyproject.toml`
  **with a reason**, never with a bare inline `# noqa`.
- `Result`-style returns for expected failures across ports. Exceptions only for programmer error.
  No exception-as-control-flow in pipelines.
- Long work runs off the UI thread via the job scheduler; the Qt event loop is never blocked.
- Structured logging via `structlog` or the stdlib `logging` module with explicit fields. Never log an API key, a prompt
  containing user images, or a full mesh.

## Build and CI

- Local: `ruff check`, `mypy`, `pytest -m "not integration"`, `lint-imports`. Wire a pre-commit hook.
- CI on Windows (GitHub Actions) mirrors the local gate, plus coverage report and a nightly mutation +
  eval run.
- Set `CI=true` so snapshot tooling never tries to open a diff window on an agent.
- Releases are versioned and carry generated release notes.

## Repository hygiene

- Trunk-based with short-lived branches. Conventional commits. Squash on merge.
- Every architectural decision gets an ADR in `docs/adr/`, numbered, with Context / Decision /
  Consequences / Status. Superseding an ADR is normal and healthy; deleting one is not.
- `CLAUDE.md` at the repo root captures the conventions an agent needs to follow them without being told
  each session.
