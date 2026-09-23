# Making a shape from a picture

The other half of "turn my ideas into printable models". A bracket with holes is
a parametric part and ModelPop builds it from a description already. A dragon is
not a part, and no vocabulary of boxes and fillets will ever describe one.

This is the path for those: hand it a photo or a drawing, get a mesh.

## Why it is a separate binary

**This was planned as a Python environment with PyTorch, and the spike overturned
it.** The measurement is in `docs/research/spike-image-to-3d.md`; the short
version is that **no Python image-to-3D model in this class installs on Windows
without compiling CUDA extensions**, and the Python TRELLIS.2 needs 24 GB of
video memory against this machine's 16.

So it runs as a **native binary driven by a subprocess** - structurally the same
adapter as the Bambu Studio slicer (ADR-0006), and for the same reasons: the
capability lives outside the process, so a crash, a hang or a missing install is
a message rather than a dead application.

That is a better fit than the Python route ever was. Nothing about ModelPop's own
environment changes, there is nothing to pin, and a broken graphics driver takes
out one feature rather than the app.

If it is not installed, the feature is **unavailable, not broken**: the button is
disabled and Settings says which piece is missing. "No binary", "no weights",
"half the weights" and "the card is busy" each get their own sentence, because
each needs a different thing from you.

## Setting it up

`trellis.cpp` - MIT, prebuilt Windows CUDA binaries, quantised weights,
explicitly engineered to fit a 16 GB card.

**1. The binary.** Download `trellis-cuda-windows-x64.zip` from
<https://github.com/pwilkin/trellis.cpp/releases/latest> and unzip it to:

```
%LOCALAPPDATA%\ModelPop\trellis\runtime\
```

Take the **standard `cuda`** package, not `cuda12`. The standard one is the CUDA
13.1 build for Turing and newer; `cuda12` is for Pascal and Volta. An RTX 4090
is Ada, so it wants the standard one. **Use v0.6.0 or later** - anything at or
below 0.5.4 has a bug that produces holes and corrupted geometry.

No compiler, no CUDA toolkit, no Python. The bundle ships the CUDA runtime and
the only requirement is the NVIDIA driver.

**2. The weights.** Ten GGUF files, about 10 GB, from the **q8** directory of
<https://huggingface.co/ilintar/trellis2-gguf>, into:

```
%LOCALAPPDATA%\ModelPop\trellis\models\
```

q8 is "near-lossless"; q4 is 6.6 GB with slight quality loss; the f16 default is
16.5 GB and there is no reason to prefer it here.

**3. Or point at an install you already have:**

```powershell
$env:MODELPOP_TRELLIS_CLI    = "D:\tools\trellis\trellis-cli.exe"
$env:MODELPOP_TRELLIS_MODELS = "D:\tools\trellis\models"
```

Check it in **File > Settings**, which names exactly what is missing.

## Which model, and the licence

| | |
|---|---|
| **Model** | `microsoft/TRELLIS.2-4B` - **MIT**, ungated, no territorial clause |
| **Runner** | `pwilkin/trellis.cpp` - **MIT** |
| **Weights** | `ilintar/trellis2-gguf`, a format conversion of the above |
| ~~Hunyuan3D~~ | **Excluded**: its licence bars the United Kingdom territorially (ADR-0004) |
| ~~Stable Fast 3D~~ | **Excluded**: gated, revenue-capped community licence |

**One caveat worth recording.** The GGUF repository is tagged `license: other`
with a card that says "see the source model". The weights are a conversion of
MIT-licensed TRELLIS.2 weights, so MIT flows through - but the tag does not
evidence that on its own. Converting the GGUFs from `microsoft/TRELLIS.2-4B`
yourself would give a chain that speaks for itself.

**Pixal3D** (TencentARC, MIT and ungated - *not* the Hunyuan3D licence) is
supported by the same binary via `--model pixal3d`. A future option at no
architectural cost.

## What the app does with the result

- **Asks for 1024, not 1536.** The project only claims the 1024 cascade fits a
  16 GB card. Asking for the highest detail setting gets 1024 and a note saying
  so, rather than an out-of-memory error four minutes in.
- **Removes the background** before generating. A photo's background otherwise
  becomes part of the model, which is the commonest way an image-to-3D result
  comes out wrong. An already-matted image keeps its alpha and skips the
  remover, which is also the way round an open bug in it.
- **Gives it a size.** A picture has no scale. The generator works in a
  normalised box and returns a model **one unit across** - read as millimetres,
  a grain of sand. So the result is scaled to 100 mm by default and the user is
  told the size was *chosen, not measured*, with a pointer at Resize. That is
  exactly what a ruler in the shot is reaching for.
- **Records provenance.** Which model, which seed, which image. Six months later
  "did I make this or did a model?" has no other answer.
- **Honours a seed, but not bit-for-bit.** The same seed gives the same shape.
  Measured, it does not give the *same file*: 141,214 triangles against 140,856
  for one seed, a quarter of a percent apart. GPU arithmetic is not
  reproducible - reductions and atomics finish in whatever order the scheduler
  chose - so the flow lands fractionally differently and the mesh extraction
  rounds differently. Fine for what seeds are for; not a basis for claiming
  reproducibility.
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

## Measured on this machine

A 512x512 drawing, `--res 512`, on an RTX 4090 Laptop (16 GB):

| | |
|---|---|
| Time | **36 seconds**, including loading the weights |
| Output | 49,848 faces, a textured GLB of 1.8 MB |
| At `--res 1024` | about 52 seconds, roughly 141,000 faces |

The binary reports its stages as it goes, so the window shows progress rather
than freezing. It also writes a `.ply` and a base-colour `.png` beside the GLB,
which ModelPop ignores - it wants the geometry.

## Once you have a mesh

It goes into the workspace like any other model, so everything downstream
already works on it: repair, scale to a stated size, place on the bed, check
printability, slice, and watch it print. That is the point of the port - nothing
downstream knows or cares that a model made the shape.
