# ADR-0010 — A picture becomes a mesh through a native binary

**Status:** Accepted
**Date:** 2026-09-24
**Evidence:** `docs/research/spike-image-to-3d.md` — every claim verified against the project's own
source, licence file or package index, and then against the binary itself.
**Amends:** ADR-0004, which named TRELLIS.2 as the primary generator on the assumption it could be
installed here.

## Context

`CLAUDE.md` and `docs/00-plan.md` both named **TRELLIS.2** as the generator, reached from a separate
Python environment with PyTorch. That plan was written from reading. The spike ran it against this
machine and it does not hold:

- **TRELLIS.2 needs 24 GB of video memory.** Its README says so in as many words. This machine has
  16.
- **Its Windows build is broken upstream**, with issues open for months, and its own README says it
  is *"tested only on Linux"*.
- **TripoSG is not on PyPI**, and its mesh-extraction path hard-imports a package that ships
  source-only, stops at Python 3.11, and has not been released since May 2024.
- Generalising: **no Python image-to-3D model in this class installs on Windows without compiling
  CUDA extensions.** Not one.

A PyTorch environment was built before this was measured. It worked — CUDA visible, the 4090
detected — and had nothing to run.

## Decision

### 1. `pwilkin/trellis.cpp`, driven as a subprocess

A C++/GGML reimplementation of the TRELLIS.2 pipeline with prebuilt Windows CUDA binaries and
quantised weights. No compiler, no CUDA toolkit, no Python — the bundle ships the CUDA runtime and
wants only the NVIDIA driver.

It is **engineered for 16 GB** rather than fitting by luck: models load and free per stage, and the
1024 cascade is explicitly claimed to fit the card.

### 2. This is the Bambu adapter's shape, not a new kind of thing

A native binary behind a `subprocess` is exactly ADR-0006. The capability lives outside the process,
so a crash, a hang or a missing install is a message rather than a dead application. Nothing about
ModelPop's own environment changes and there is nothing extra to pin.

It is better than the Python route on its own terms too: the binary reports its stages on stdout, so
a progress bar comes free — which the Bambu CLI, saying nothing at all, does not give us.

### 3. Resolution is capped at 1024, and the cap is reported

Only 1024 is claimed to fit 16 GB. Asking for the highest detail gets 1024 and a note saying so,
rather than an out-of-memory error four minutes in. Quietly giving less than was asked for would be
worse than either.

### 4. Words alone are refused, not approximated

The backend is image-to-3D. A description is turned away with a sentence explaining why, because a
dragon that is not the dragon you asked for is worse than no dragon.

### 5. A seed gives the same shape, not the same file

**Measured:** the same seed produced 141,214 triangles on one run and 140,856 on the next — a quarter
of a percent apart. GPU arithmetic is not reproducible; reductions and atomics finish in whatever
order the scheduler chose. That is fine for what a seed is for, and it is not a basis for claiming
reproducibility, so neither the tests nor the documentation claim it.

### 6. Licence

`trellis.cpp` is MIT. `microsoft/TRELLIS.2-4B` is MIT and ungated, with **no territorial clause** —
the thing that disqualified Hunyuan3D under ADR-0004. Stable Fast 3D is excluded separately: gated,
revenue-capped community licence.

**One weak link, recorded rather than glossed:** the GGUF repository is tagged `license: other` with
a card that says "see the source model". The weights are a format conversion of MIT ones, so MIT
flows through, but the tag does not evidence that by itself. Converting the GGUFs from the source
model directly would give a chain that speaks for itself.

## Consequences

**Good.** It works, on this machine, today: 36 seconds for a draft and about 52 for a full-resolution
model on the 4090. Everything downstream already handles the result — repair, resize, bed placement,
readiness, slicing, print preview — because it arrives as an ordinary mesh through the existing port.
Nothing was added to ModelPop's dependencies.

**Bad.** A 10 GB download and a 700 MB binary the user installs themselves; it is not `pip install`.
The app depends on a third-party binary's command-line surface, which can change — though it is one
adapter and one file. And roughly three gigabytes of PyTorch were downloaded and deleted, which is
the honest cost of having built before measuring.

**Watch.** `--res 1536` is unproven on 16 GB and is not offered. The background remover has an open
upstream bug; the fallback is the simple keyer, which cuts specular highlights out of the alpha and
turns them into holes — so the user is told when they choose it. **Pixal3D** (MIT, ungated) runs on
the same binary via a flag, and would be a backend change of one word.

## Alternatives considered

**WSL2 with the upstream Python TRELLIS.2.** Rejected: a second operating system, a 24 GB memory
requirement against 16, and it breaks the single-process premise of ADR-0007.

**Install Visual Studio Build Tools and compile TripoSG's dependencies.** Buildable, probably. It
would mean adopting an unmaintained 2025 model with worse output, plus a compiler toolchain as a
prerequisite for one feature.

**A hosted API.** Rejected on the project's own first constraint: the only running cost is AI
credits for text, and the whole point is that this runs on the user's own card.
