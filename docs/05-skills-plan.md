# ModelPop — Claude Code skills plan

Reviewed all three sources you named: the official `anthropics/skills` repo, the "15 skills that stuck"
article, and skillsmp.com. Plus the official plugin marketplace already registered on this machine
(310 plugins available; only `frontend-design` and `theme-factory` currently installed).

## The headline finding

**There is no existing skill for what we are building.** The official repo has 19 skills and not one
touches 3D, CAD, meshes or printing. skillsmp.com indexes millions of files and has no skill for this
stack either. The good news after ADR-0007: the relevant CAD skills found there are **Python-centric**,
so they are now directly usable rather than mere reference.

So the plan has two halves: **install a small number of proven workflow skills**, then **write the
domain skills ourselves**. The second half is where the real leverage is, and it is also why
`skill-creator` is on the install list.

---

## Tier 1 — install now, from the official marketplace

These come through `/plugin marketplace add anthropics/claude-plugins-official` (already registered),
which matters: they are vetted, unlike anything from a GitHub crawler.

| Plugin | Why it earns its place here |
|---|---|
| **superpowers** | Brainstorm → plan → TDD → subagent-driven development with built-in review. This is the single highest-value install. Its whole thesis — force the model to plan and test first instead of sprinting — is exactly what a multi-week geometry project needs. |
| **skill-creator** | We are going to write 6–8 domain skills. This scaffolds them correctly (the YAML frontmatter fails silently if wrong) and, more importantly, it can **measure** a skill with evals. |
| **mattpocock-skills** | TDD, spec→ticket flows, code review split across parallel sub-agents, domain modelling. Complements superpowers rather than duplicating it. |
| **feature-dev** | Codebase exploration, architecture design and quality review agents — the loop we want for each vertical slice. |
| **pr-review-toolkit** | Specialised reviewers for tests, error handling, type design, simplification. Geometry code rewards a reviewer that looks specifically at error handling. |
| **pyright-lsp** | Python language server for real code intelligence. Non-negotiable for a large package. |
| **github** | Issue and PR management against `robsaddler/ModelPop`. |
| **mcp-server-dev** | We will likely expose the CAD kernel and slicer as an MCP server so agents can drive ModelPop. This covers tool design and deployment models. |
| **security-guidance** | Pattern warnings on edits plus a diff reviewer that catches injection. We are executing model-generated Python; this is not optional. |
| **commit-commands** | Commit and PR hygiene. |

Install: `/plugin install <name>@claude-plugins-official`.

**Also worth it:** `claude-api` from `anthropics/skills` — model ids, pricing, streaming, tool use and
prompt caching for the Anthropic Python SDK. Install it: `/plugin marketplace add anthropics/skills`
then `/plugin install claude-api@anthropic-agent-skills`.

---

## Tier 2 — third-party, read the SKILL.md before installing

From skillsmp.com and GitHub. All useful, none vetted. See the safety note at the bottom.

| Skill / repo | Value to us |
|---|---|
| `github/awesome-copilot` → **`create-architectural-decision-record`**, **`poka-yoke`** | ADR template, and mistake-proof API design. Language-agnostic. |
| **`addyosmani/agent-skills`** | TDD, code review, ADRs, planning, spec-driven development, incremental implementation, code simplification. Broad and well-made. |
| **`earthtojake/text-to-cad`** → `cad`, `dfam-check`, `bambu-labs`, `gcode` | Now **directly runnable**: STEP-first build123d modelling, design-for-additive rules, and Bambu LAN job submission — the same stack we chose. Promoted from reference to tooling by ADR-0007. |
| **`flowful-ai/cad-skill`** → `parametric-3d-printing` | CadQuery/OCC → STL + 3MF with FDM rules baked in. Same stack. |
| **`pzfreo/build123d-mcp`** / **`jdilla1277/agentcad`** | The render→measure→fix loop for Pipeline A, already built against build123d. Mine the prompt structure and gates. |
| **`obra/superpowers`** (direct repo) | If you want the newest version ahead of the marketplace copy. Prefer the marketplace one otherwise. |

**`interview-me`** (from the article) is worth a look for one specific moment: before locking the scope
of each phase. Its whole job is to interrogate you until the spec has no holes. That is genuinely useful
at a phase boundary and useless day to day.

**Skip** from the article: GSD (overlaps superpowers, heavier), Context Mode (we are not context-bound
yet; revisit if sessions start dying), the content/Twitter/meeting skills (irrelevant here), and
Caveman — the article's own warning is correct, it trims output tokens only.

---

## Tier 3 — domain reference, read rather than install

Useful for their **content** rather than as installed tooling. Read them, mine them, cite them in our
own skills.

| Source | What to mine from it |
|---|---|
| `earthtojake/text-to-cad` → `bambu-labs`, `dfam-check`, `cad`, `gcode` | Bambu LAN FTPS + MQTT job submission (with a dry-run-by-default safety design worth copying), and design-for-additive-manufacturing rules. |
| `andymai/brepkit` (18 skills) | B-rep doctrine: solid verification, boolean failure diagnosis, tessellation, numerical robustness, parity benchmarking. Kernel-agnostic and directly applicable. |
| `flowful-ai/cad-skill` → `parametric-3d-printing` | FDM design rules encoded for an LLM: wall thicknesses, what to avoid. Good raw material for our codegen prompt. |
| `HKUDS/CLI-Anything` → `cli-anything-threemf`, `cli-anything-blender` | A Python 3MF editor that preserves slicer metadata, and a stateful Blender CLI. Both directly usable now. |
| `andymai/gridfinity-layout-tool` → `geometry-debugging`, `print-export` | Non-manifold/watertight export checks tuned for BambuStudio/OrcaSlicer. |
| `Zigfreed107/Graphite` → `cad-code-review` | A CAD-app review checklist: tool state, selection, snapping, render pipeline, scene management. Written for C#/Helix, but the concerns are framework-agnostic. Zero stars — a template to adapt, not a dependency. |

---

## Tier 4 — the skills we write (the actual leverage)

This is the half that matters. Each one encodes knowledge this project will otherwise re-derive every
session. Write them with `skill-creator`, keep them in `.claude/skills/` in the repo so they are
versioned with the code, and give each one evals.

| Skill | What it encodes | Write it in |
|---|---|---|
| **`modelpop-conventions`** | Layering rules, the command-bus invariant, `Result` usage, naming, what must never import what. The rules from `01-architecture.md` in enforceable form. | Phase 0 |
| **`modelpop-geometry-invariants`** | The property-based contracts for mesh operations, the tolerance conventions, how to write a golden-mesh test, the degenerate cases that break kernels. | Phase 1 |
| **`modelpop-bambu-3mf`** | Ground truth for the Bambu project 3MF: file layout, `model_settings.config` per-object comma-joined values, `project_settings.config` resolution, support enforcer/blocker subtypes, `slice_info.config`. Already half-written in `docs/research/spike-bambu-cli.md`. | Phase 2 |
| **`modelpop-slicer-cli`** | The verified CLI invocation, the argument-quoting trap, `result.json` semantics and every field we consume. | Phase 2 |
| **`modelpop-cad-codegen`** | The build123d helper library, the prompt structure, the gate sequence, the contact-sheet convention, and the failure-feedback format. | Phase 4 |
| **`modelpop-print-readiness`** | The readiness rules, thresholds, and how a new rule is added and tested. | Phase 3 |
| **`modelpop-generation-env`** | The separate PyTorch venv, pinning conventions, GPU lease rules, how to add a new generator. | Phase 3 |

---

## Supporting setup, beyond skills

- **`CLAUDE.md`** at the repo root — conventions, build/test commands, the "never do this" list. Skills
  are for reusable expertise; CLAUDE.md is for *this repo's* facts. Run `/init` once the solution exists.
- **Custom slash commands** in `.claude/commands/` for the loops we will run hundreds of times:
  `/slice <file>`, `/eval <suite>`, `/new-stage <name>`, `/adr <title>`.
- **An MCP server exposing ModelPop itself** (`mcp-server-dev` covers how). Once the kernel and slicer
  are behind ports, exposing them as MCP tools lets an agent drive the real app — generate, measure,
  slice, critique — which is how the generate→critique loop gets tested end to end.
- **Hooks** for the mechanical gates: format on edit, run the fast test suite before a commit.

---

## Safety note on skillsmp.com

Worth being explicit, because it changes how you should use it. It is a **crawler**, not a curated
store: it indexes millions of `SKILL.md` files from public GitHub with no review. Its own terms say it
"does not endorse or verify the quality, safety, or functionality of any skill". There are no ratings —
the star counts shown belong to the *host repository*, so a trivial skill inside a large repo appears to
have 300 k stars. The index includes auto-generated bulk repos and at least one repo of leaked system
prompts.

A SKILL.md is instructions loaded straight into your agent's context, and the `npx skills add` route
also pulls whatever scripts the repo bundles. So:

1. Prefer the **official marketplace** where a skill exists in both places.
2. Otherwise read the SKILL.md on GitHub **before** installing, and check the repo is real.
3. Never install a skill that ships scripts you have not read.
4. Use skillsmp for **discovery**, then install from the source repo you have inspected.

Its read-only MCP server is a reasonable way to search it without granting anything.
