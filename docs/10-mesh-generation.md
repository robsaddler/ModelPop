# Making a shape from a picture

The other half of "turn my ideas into printable models". A bracket with holes is
a parametric part and ModelPop builds it from a description already. A dragon is
not a part, and no vocabulary of boxes and fillets will ever describe one.

This is the path for those: hand it a photo or a drawing, get a mesh.

## Why it lives in its own Python

The models that do this want PyTorch, CUDA, several gigabytes of weights, and
often a Python version ModelPop itself does not run on. Making any of that a
dependency of the application would mean:

- a multi-gigabyte install for everyone, including people who only want to slice
  an STL they downloaded;
- ModelPop pinned to whatever Python the machine-learning stack supports this
  month, which today is **not 3.14**;
- a broken CUDA install taking the whole app down rather than one feature.

So it is a **separate environment**, reached by a subprocess, exactly as the CAD
kernel is and for the same reasons. ModelPop talks to it over JSON lines on
stdout and never imports a thing from it. `src/modelpop/generation/mesh_worker.py`
runs *there*, not here, and a test asserts it imports nothing from `modelpop` -
because such an import would crash on the user's machine and nowhere else.

If it is not installed, the feature is **unavailable, not broken**: the button is
disabled and Settings says which piece is missing. "Not installed", "no CUDA"
and "no generator" each get their own sentence, because each needs a different
thing from you.

## Setting it up

One command, from the project root:

```powershell
uv run python scripts/setup_generation.py
```

It creates a Python 3.12 environment under your application data directory,
installs PyTorch built for CUDA 12.8, and then tells you what to do next. It
does **not** download model weights - those come on first use, from the backend's
own cache, and they are several gigabytes.

To put it somewhere else, or to point at an environment you already have:

```powershell
$env:MODELPOP_GENERATION_PYTHON = "D:\envs\trellis\Scripts\python.exe"
```

Check it worked in **File > Settings**, which shows exactly what the environment
contains.

## Choosing a backend

Verified in `docs/research/findings.md`. Two are supported, and the choice is
mostly settled by licence:

| Backend | Licence | Notes |
|---|---|---|
| **TRELLIS** | MIT | The default. Structured latents, good at hard surfaces as well as organic shapes. |
| **TripoSG** | MIT | Fallback. Lighter, faster, less detail. |
| ~~Hunyuan3D~~ | **Excluded** | Its licence **bars the United Kingdom territorially**. The open-source relaxation does not change that. See ADR-0004. |

The worker picks whichever is importable, TRELLIS first. Adding a third is a
function in `mesh_worker.py` and a name in the probe - nothing else knows.

## What the app does with the result

- **Caps the triangles.** These models happily produce millions and a printer
  cannot use them; the slicer only takes longer throwing the detail away. The
  ceiling is 200,000 by default and the run says when it simplified.
- **Removes the background** of a photo before generating, if `rembg` is
  installed. A photo's background otherwise becomes part of the model, which is
  the commonest way an image-to-3D result comes out wrong. Without it the run
  still happens and says it did not.
- **Records provenance.** Which model, which seed, which image. Six months later
  "did I make this or did a model?" has no other answer.
- **Holds a lease on the card.** One large model at a time. Two does not fail
  cleanly - it fails as an out-of-memory error a minute in, and sometimes takes
  the driver with it. The lease survives a crash, because a lock file nobody
  releases is worse than no lock file.

## What is deliberately not here yet

**Words straight to a mesh.** The installed backends are image-to-3D. Asking for
one from a description is refused *honestly* rather than approximated, because a
dragon that is not the dragon you asked for is worse than a message. The route
to it is text to image first, which is another model and another decision.

**Rigging, texturing, colour.** A printed model in one filament does not need
them, and the AMS colour path is multi-colour part splitting, not texture.

## Once you have a mesh

It goes into the workspace like any other model, so everything downstream
already works on it: repair, scale to a stated size, place on the bed, check
printability, slice, and watch it print. That is the point of the port - nothing
downstream knows or cares that a model made the shape.
