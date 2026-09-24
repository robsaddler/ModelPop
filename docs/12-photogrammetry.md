# Measuring a model from photographs

Walk round an object taking photographs, and get a model of it that you can print. Not a guess at
what it looks like — a *measurement* of the shape the camera saw.

This is the other half of "photo → model". The single-picture path asks a generative model to
invent a plausible object, back included. This one solves where the camera was for each photograph
and measures the surface they agree on. Both make a mesh; only one of them is evidence.

## What it needs installed

Two external programs, neither of which is in winget. Both are official Windows builds from their
own GitHub releases, and both install by unzipping.

| | Where ModelPop looks | Version tested | Licence |
|---|---|---|---|
| [COLMAP](https://github.com/colmap/colmap/releases) | `C:\Tools\COLMAP\bin\colmap.exe` | 4.2.0, CUDA build | new BSD |
| [OpenMVS](https://github.com/cdcseacave/openMVS/releases) | `C:\Tools\OpenMVS\` | 2.4.0, x64 | AGPL-3.0 |

Override either with `MODELPOP_COLMAP` and `MODELPOP_OPENMVS`; otherwise `C:\Tools`, then `PATH`.

**The usual mistake** is leaving OpenMVS in the subfolder it unzips into (`vc17/x64/Release`).
ModelPop wants the tools directly inside the folder it is pointed at, and says which ones are
missing when they are not.

Take the **CUDA** build of COLMAP if you have an NVIDIA card — feature matching is far quicker on
it. The plain OpenMVS build is the right one unless you have the CUDA toolkit installed; its CUDA
build links against a runtime that a driver alone does not provide.

## Taking the photographs

This is the part that decides whether it works, and no amount of software fixes a bad capture.

- **Walk right round the subject**, a photograph every 10–15 degrees. Each one needs to overlap its
  neighbours by well over half. Twenty-odd is where it starts being worth the wait.
- **Move yourself, not the subject.** A turntable keeps the object still and moves the background,
  which is the opposite of what the solver needs.
- **Keep the lighting the same** and the subject filling the frame.
- **Plain, shiny and transparent things do not reconstruct.** If the surface has no texture to
  match between photographs, nothing here can invent one. A matt, patterned object is easy; a clean
  white mug is impossible.
- **Put a ruler in shot** if you want the size to be real — see "Scale" below.

## Using it

**File → Measure one from several photographs**, then choose the files or the folder. The dialog
tells you what is wrong with the set before anything starts, because the alternative is finding out
several minutes later.

**Effort** trades time against detail. Draft is the one to use first: it is quick and it tells you
whether the capture was any good, which is the only question that matters on the first run.

## What comes back

A mesh, in the workspace, like anything else — so repair, resize, place on bed, readiness and
slicing all work on it immediately.

Three things about it are worth knowing.

**It has holes.** Photogrammetry only sees what the camera saw; wherever you did not point it,
there is nothing. **Repair** closes them.

**It is big.** A third of a million triangles is normal. ModelPop says so rather than quietly
throwing two thirds of your measurement away — **Simplify** is there when you want it.

**It has no scale.** This is the one that surprises people. The solver fixes the shape up to an
arbitrary factor; nothing in a photograph says how big anything was. So the model comes out
whatever size you asked for, and the note on it says the size was *chosen*.

To get a real size, put a ruler or a coin in the shot and use **Measure it from the photo** on one
of the images. The note then says the size was *measured*, which means it can be checked with
calipers — and that distinction is deliberate, because on screen the two look identical.

## How long it takes

Measured on this machine, 24 photographs at 1024×768, draft quality: **about 90 seconds**. Two
thirds of that is one stage — filling in the surface between matched points — which is why the
progress bar moves in the uneven way it does. It is not stuck.

Real photographs at full camera resolution take considerably longer. Start with draft.

## When it does not work

**"The photographs could not be pieced together"** is the failure that matters, and it is almost
always the capture rather than the software: too little overlap, or a subject too plain or too
shiny to find detail on. More photographs, moving a little between each.

**Only some of the photographs were placed.** The result says how many out of how many, and warns
you when it is a small fraction — a model built from six of forty photographs looks perfectly
confident and is a model of almost nothing.

**A lot was discarded as background.** The table, the floor, whatever else was in shot. ModelPop
keeps the largest connected lump and tells you what fraction went. If it threw away the wrong thing,
the subject was probably touching something else it could not tell apart.

See `docs/adr/0012-reconstruction-from-photographs.md` for why it is built this way, and
`docs/research/spike-photogrammetry.md` for the measurements behind it.
