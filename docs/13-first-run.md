# First run: what to set up, and how to check it works

Everything optional is genuinely optional. The app starts with nothing configured, and every part
that needs something says which piece is missing rather than failing at the click. So set things up
in the order you want to use them, and check each as you go.

Start it with:

```
.venv\Scripts\pythonw.exe -m modelpop.ui.main
```

or `modelpop` if the package is on your path. `pythonw` rather than `python` only means you do not
get a console window behind it.

---

## What needs setting up

| To do this | You need | Where |
|---|---|---|
| Open, inspect, repair, resize, model, save | **nothing** | |
| Slice, and check the toolpath | Bambu Studio | installed already |
| Make one from a picture | the trellis.cpp binary and ~10 GB of weights | `docs/10-mesh-generation.md` |
| Measure one from photographs | COLMAP and OpenMVS | `docs/12-photogrammetry.md` |
| Describe a part, or change one by saying so | an Anthropic API key | **File → Settings** |
| Search for a model to start from | a free MyMiniFactory or Thingiverse key | **File → Settings** |
| Send a job to the printer | its address, serial and access code | **File → Settings** |

Keys go to the Windows Credential Manager, never to a file in the repo. An exported
`ANTHROPIC_API_KEY` takes precedence over the stored one.

**File → Settings** also reports what it found: which graphics card the viewport is drawing on, and
whether the picture-to-model environment is complete. If something is not working, read that panel
before anything else — it is specific on purpose.

---

## Checking each piece, in the order it is worth doing

Each of these is a couple of minutes and ends with something you can see.

### 1. It runs, and it can read a model *(no setup)*

**File → Open** anything — an STL from a download, or `out/model.stl` in the repo if there is one.

You should get: the model on a build plate drawn to scale, and a **Print readiness** panel that has
an opinion about it. Orbit with the left mouse button.

*If the viewport is black or empty*, that is the thing to report — everything else depends on it.

### 2. You can model something from nothing *(no setup)*

In the **CAD tools** tab: **Box**, then **Round**, then **Hollow** with a face left open.

You should get: a feature tree with three steps, each readable as a sentence, and **undo** stepping
back through them. Change the box's size and everything after it rebuilds.

Then **View → Cut it open** (Ctrl+K) and drag the slider through it — this is the only way to see
whether the hollow really came out at the wall thickness you asked for.

### 3. It slices *(needs Bambu Studio)*

With that model open: **Print → Slice**, choose an output folder.

You should get: a predicted print time, layer count, and findings about anything printing into mid
air. The G-code is real — Bambu Studio produced it.

Then **Print → Watch it print** to scrub the toolpath.

### 4. A picture becomes a model *(needs the generation environment)*

**File → Make one from a picture.** Any photo or drawing on a plain background.

Set **Make** to **3 shapes** the first time. It takes about a minute each, and the point is that
asking three times gives three different answers — the first is rarely the best.

You should get: a **Shapes made this session** list under the readiness panel. Click between them;
each appears in the viewport at full size. Each row shows its seed, so one you like can be asked
for again.

Then try **Rescue the detail...** on one of them: the model's colour becomes actual relief, so the
detail survives slicing instead of printing as a smooth blob.

### 5. Photographs become a measurement *(needs COLMAP and OpenMVS)*

The capture matters far more than the settings. Walk right round an object taking a photo every
10–15 degrees, each overlapping its neighbours by well over half. Move yourself, not the object.
Twenty-odd photographs. Matt and patterned works; plain, shiny and transparent do not reconstruct
at all.

**File → Measure one from several photographs**, choose the folder, leave it on **Draft**.

You should get: a model in about 90 seconds, and a message saying how many photographs it could
place. That number is the one that matters — six of forty looks confident and is a model of almost
nothing.

It will have holes where you did not point the camera. **Repair** closes them.

### 6. Describing a part *(needs an Anthropic API key)*

In the **CAD tools** tab, the box at the bottom. With nothing open it says **Make it**.

Try: *a wall bracket for a 35 mm pipe with two M4 holes 40 mm apart*.

You should get: ordinary steps in the feature tree, each undoable one at a time — not a blob. Then
type *round the outside corners by 3 mm* and watch it join the same tree.

### 7. Sending it to the printer *(needs the printer's details)*

Get the address, serial and access code off the printer's own network screen and put them in
**File → Settings**.

**Leave "Really send jobs to this printer" unticked the first time.** With it off, **Print → Send it
to the printer** tells you exactly what it would have sent and sends nothing. That is the safe way
to check the details are right.

Then tick it and send for real. You get a choice at that moment: **send the file only** (the
default — it lands on the printer and you start it from its screen) or **send it and start
printing**. Uploading is reversible; a print is not, and nothing in ModelPop can stop one.

**Print → What is the printer doing?** polls it while it runs.

---

## The scenario worth doing end to end

Once the pieces work individually, the run that proves the whole thing:

1. **Photograph a real object** — twenty-odd shots, right round it.
2. **Measure one from several photographs** → a model of it, at draft.
3. **Repair** to close the holes where the camera did not look.
4. **Resize** to the size you actually want it.
5. **Place on bed**, then read the readiness panel and act on whatever it says.
6. **Slice** — check the predicted time and that nothing prints into mid air.
7. **Watch it print** to see the order it builds in.
8. **Send it to the printer**, file only.
9. Start it from the printer and **watch it print** from the app.

That is a real object, measured, prepared, verified and printed without leaving the application —
which is the whole thesis of the project.

---

## If something does not work

The app is deliberately specific about what is missing. **File → Settings** reports the state of
the graphics card and the generation environment; the other failures name the piece and the file
that explains it.

Two things worth knowing because they surprise people:

- **A model from a picture, or from photographs, has no scale.** Neither carries one. The model
  comes out the size you asked for, and the note on it says the size was *chosen*. Put a ruler in
  the shot and use **Measure it from the photo** to get a size that can be checked with calipers —
  the app never lets one pass for the other.
- **The viewport draws on integrated graphics** on a laptop with a discrete card, and nothing in
  the app can change that — only the graphics driver's control panel can. It is comfortably fast
  enough; measured at 70–96 FPS on 400,000 triangles.
