# Spike — what can actually generate a mesh on this machine?

**Run 23 September 2026.** Every claim verified against the project's own source, licence file or
package index on the day. Nothing was installed during the spike; "verified" means read from the
primary source, not executed.

**The machine:** Windows 11, RTX 4090 **Laptop** (16 GB), driver 616.92, Python 3.12 available.
**The bar:** MIT or Apache, no territorial exclusion (the user is in the UK), and no build step that
needs Visual Studio.

## The finding

**No Python image-to-3D model in this class installs on Windows without compiling CUDA extensions.**
Not one. That is the honest answer and it overturns the plan.

| Candidate | pip-installable on Windows | Compiler needed | Licence | Fits 16 GB |
|---|---|---|---|---|
| TRELLIS 1 | No - `setup.sh` is bash, uses `apt` and `sudo` | MSVC + CUDA toolkit | MIT | marginal |
| **TRELLIS.2** | No - same bash installer, six compiled components | MSVC + CUDA toolkit | MIT | **No: 24 GB stated** |
| TripoSG | **No** - not on PyPI at all | MSVC + `nvcc` | MIT | Yes |
| Stable Fast 3D | - | - | **Gated, revenue-capped** | - |
| Hunyuan3D | - | - | **Excludes the UK** (ADR-0004) | - |

Three things this changes.

**TRELLIS.2 needs 24 GB.** Its README says so in as many words: *"An NVIDIA GPU with at least 24GB
of memory is necessary."* The plan named it as the primary generator against a 16 GB card. That was
never going to work.

**Its Windows build is broken upstream and nobody has fixed it.** Issues #100 "fix windows build"
and #107 "Fix o-voxel compilation on Windows (MSVC narrowing conversions)" are both open, months
old. The README says *"The code is currently tested only on Linux."*

**TripoSG is not pip-installable, contrary to how its README reads.** `pypi.org/pypi/triposg/json`
returns 404. Worse, `triposg/inference_utils.py` has a hard top-level `from diso import DiffDMC`
on the mesh-extraction path, and `diso` ships **sdist only** - no wheels at all, classifiers stop at
Python 3.11, last release May 2024. It would need MSVC and `nvcc` to build. Its `requirements.txt`
also pins `numpy==1.22.3`, which predates Python 3.12 and has no wheel for it.

The repository has also not been touched since April 2025.

## The way out: trellis.cpp

`pwilkin/trellis.cpp` - MIT, actively developed, last release v0.6.0 in August 2026.

A C++/GGML reimplementation of the whole TRELLIS.2 pipeline with **prebuilt Windows x64 CUDA
binaries** and **quantised GGUF weights**. No Python at runtime, no compiler, no CUDA toolkit - the
bundle ships the CUDA runtime, and the only requirement is the NVIDIA driver.

It solves the 16 GB problem deliberately rather than by luck. From its own roadmap:

> the cascade is enabled by FlashAttention with padded K/V ... **it fits the 16 GB card** (the manual
> softmax wanted a single ~18 GB score-matrix alloc at the sparse-structure stage)

and models are loaded and freed **per stage**, so all ten never sit in memory together. Quantised
weights are 10 GB on disk at q8 ("near-lossless") against 16.5 GB at f16.

It offers both a CLI and an HTTP server:

```
trellis-cli input.png out.glb --res 1024 --seed 7 --models <dir>
```

```
POST /generate   multipart image, returns model/gltf-binary
```

**And it fits the architecture better than the Python route ever did.** An out-of-process native
binary driven by `subprocess` is exactly the shape of the sanctioned Bambu Studio CLI adapter
(ADR-0006), not a new kind of thing. Unlike the Bambu CLI it even writes parseable stage progress to
stdout, so a progress bar is free.

### What to use

- **Package:** `trellis-cuda-windows-x64.zip` from the latest release. **Not** `trellis-cuda12-*`,
  which is the CUDA 12.9 build for Pascal and Volta; the standard `cuda` package is CUDA 13.1 for
  Turing and newer, and an Ada 4090 is newer.
- **Weights:** `ilintar/trellis2-gguf`, the **q8** subdirectory, ten files, 10.01 GB.
- **Version:** v0.6.0 or later. Issue #33 - severe holes and corrupted geometry on the CUDA build -
  was fixed there. Anything at or below 0.5.4 is unusable.

### Known problems, and which apply here

| Problem | Applies? |
|---|---|
| #19 CUDA crash on RTX 5090 | No - that is Blackwell; this is Ada. |
| #27, #35 Vulkan crashes on AMD | No - CUDA on NVIDIA. |
| #41 BiRefNet background removal produces a flat image | **Open.** Fall back to `--bg-removal threshold`, or pass an already-matted RGBA image, which skips BiRefNet entirely. |
| White-background keying cuts specular highlights out of the alpha, and the flow then generates holes there | Real. Matters for photos of shiny objects. |
| First request is slow because weights load | Expected. The adapter needs a generous first-call timeout. |

### The licence chain, and one caveat to record

`trellis.cpp` is MIT. `microsoft/TRELLIS.2-4B` is MIT and ungated - *"Permission is hereby granted,
free of charge, to any person ... without restriction"*, with **no territorial clause**. The UK is
not excluded, which is the thing that disqualified Hunyuan3D.

**But the GGUF repository `ilintar/trellis2-gguf` is tagged `license: other`** with a card that says
"see the source model" and confusingly names TRELLIS-image-large rather than TRELLIS.2-4B. The
weights are a format conversion of MIT weights, so MIT flows through - but the tag does not evidence
that on its own. Either accept it with this note recorded, or convert the GGUFs from
`microsoft/TRELLIS.2-4B` directly to get a chain that speaks for itself.

## Also worth knowing

**Pixal3D** (TencentARC, SIGGRAPH 2026) is **MIT and ungated** - it is *not* under the Hunyuan3D
licence and carries no territorial restriction. It is a TRELLIS.2 fine-tune with pixel-aligned
conditioning, and `trellis.cpp` already supports it via `--model pixal3d` with five extra GGUFs. A
good future option on the same adapter, at no architectural cost.

## What this cost

The PyTorch environment built earlier in the session - Python 3.12 with torch 2.11.0+cu128, about
three gigabytes - turns out to be unnecessary. The probe that proved it works also proved it has
nothing to run. Keeping it would be keeping a path that cannot be completed on this platform, so it
goes, and the adapter is rewritten against the binary.

Worth recording plainly: this is the second time in this project that a measured spike has overturned
a plan written from reading (the first was ADR-0007, which moved the whole app off C#). Both times
the plan was reasonable and the measurement was different.

---

## Addendum: what running `--help` changed

The binary was downloaded and its help read, rather than the adapter being left
to trust a README. Two things were wrong.

**There is no "do not remove the background".** The choice is between the default
`auto` - which keeps an already-matted image's alpha and otherwise uses the good
matting model - and `--bg-removal threshold`, a crude keyer. The adapter had a
boolean that mapped "keep the background" onto the threshold keyer, which still
removes it, just badly. The port now names the two real choices.

Better still, the help states the failure mode in its own words: *"The plain
threshold matte cuts out specular highlights, which the flow then turns into
holes."* That is now what the user is told when they pick it.

**It falls back to the processor silently.** `--require-gpu` exists precisely to
stop that, and without it a generation does not fail, it takes hours - which is
worse. The adapter passes it by default.

One thing it made unnecessary: `--decim` already defaults to simplifying to 300k
faces at 1024 and 150k at 512. That is a sane print budget, so the adapter's own
triangle ceiling was removed rather than becoming a second opinion with no better
information behind it.

Confirmed as built: positional `<image> <out.glb>`, `--models`, `--seed`,
`--res`. The seed default is 42, so omitting it is still deterministic.
