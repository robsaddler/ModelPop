# Using ModelPop

Task-oriented, and honest about the edges. If something here does not match what the app does,
the app is right and this is stale — say so.

Run it with `.venv\Scripts\pythonw.exe -m modelpop.ui.main`, or `modelpop` if the package is
installed on the path.

---

## The window

Left is the **viewport**: the P2S build plate, its printable envelope as a wireframe, and whatever
model is open. Right is a pair of tabs.

**Print readiness** answers "would this print?" — overall size, watertightness, wall thickness,
things floating in mid air — and holds the buttons that fix those: Repair, Resize, Place on bed,
Simplify, Slice.

**CAD tools** is the model itself: the shapes, the operations, the plain-English box, and the
feature tree showing how the part was built, in order.

---

## Six ways to get a model

| Start with | How |
|---|---|
| A file you have | **File → Open** — STL, 3MF, OBJ, STEP |
| Somebody else's design | **File → Find a model to start from** (Ctrl+F) |
| A photograph or a drawing | **File → Make one from a picture** |
| Several photographs of a real object | **File → Measure one from several photographs** |
| A description | Type it in the **CAD tools** tab and press **Make it** |
| Nothing at all | **Box**, **Cylinder**, **Sphere** or **Profile...** |

The last two give you a **feature tree**: a parametric model where every step is recorded, every
step undoes, and changing an early step rebuilds everything after it. The first four give you a
**mesh**: printable, repairable, resizable, but not parametric — you cannot go back and change its
width, because nothing recorded a width.

That difference is the single most important thing to understand about the app, and it is why
"describe a new part" is worth reaching for over "generate a part": a described part lands in the
feature tree and the toolbar can go on refining it.

---

## Modelling something

### Shapes

**Box**, **Cylinder** and **Sphere** each take a size and a position, and a **Cut** tick. Positions
are measured from the centre of the part, and every shape is centred on the origin — so a 60 mm box
runs from −30 to +30. Ticking Cut removes the shape instead of adding it, which is how you drill a
hole: a cylinder, longer than the thing it passes through, with Cut on.

### Profiles

**Profile...** is the operation the three primitives cannot cover between them. Type corners, one
per line, as `x, y`. The preview shows the shape and tells you what it encloses; presets give you
something to edit rather than an empty box.

Then choose what to do with it:

- **Give it thickness** — a bracket, a gasket, a nameplate, anything with a constant cross-section.
- **Spin it round** — a vase, a knob, a wheel, a bottle. The corners become *radius from the axis*
  and *height*, and the axis is the dashed line down the left of the preview.
- **Push it along a path** — a grab handle, a cable channel, a length of trim, a bent tube.
  Anything with a constant cross-section that does *not* run in a straight line. The corners are
  the cross-section; a second box takes the path, three numbers a line. The cross-section is placed
  square to the start of the path automatically, so there is no plane to choose.
- **Blend it into another** — a tapered pot, a funnel, a square duct meeting a round one. Anything
  whose cross-section *changes* on the way up. Here the corners take three numbers too: the corner,
  then the height its outline sits at. Every line sharing a height belongs to the same outline.

Whichever you pick, the hint under the box says what the numbers mean, and a preview shows the
shape before you commit to it. A blend shows all its outlines at once, on one scale, because the
whole question there is how the shape changes between them.

**About the bend radius on a sweep.** A mitred corner is not a sharp corner, it is a solid folded
through itself — measured here at less than half the volume it should be, with no error anywhere.
So there is always some bend, and it is quietly eased to whatever the straight runs between the
corners can actually give up. The app says so when it does that.

Either way the finished shape is centred on the origin. The corners describe the shape, not where
it sits; use the position fields, a move, or a drag to place it.

### Changing what is there

**Round** and **Bevel** take a size and a choice of edges (all, vertical, horizontal, top, bottom).
**Hollow** takes a wall thickness and, optionally, a face to leave open so the inside can drain —
which you want for anything you print.

**Resize** scales the whole part so its tallest dimension is the size you ask for. It is recomputed
on every rebuild, so "make it six inches" stays six inches after you change something earlier.

**Text...** puts a string on a face, raised or engraved. Raised lettering is what **Split into two
colours...** later separates.

### Doing it more than once

**In a row** copies the *last shape you added*, spaced out. A row of mounting holes is one drilled
hole and this. **In a ring** spaces copies evenly round the centre — a bolt circle is one off-centre
hole and this. **Mirror** reflects the whole part and keeps both halves, so you model one side of a
symmetrical part and let the other side follow.

All three stay parametric. Change the plate and the holes move with it.

### Saying what you want

The box at the bottom of the CAD tools tab takes plain English. With nothing open it says **Make
it** and builds a part; with something open it says **Change it**. Either way what comes back is the
same typed operations the buttons emit, so it lands in the feature tree and undoes one step at a
time. Needs an API key — **File → Settings**.

It can only ask for operations that exist. If what you want is not in the list it will say so
rather than approximate.

---

## Measuring

**View → Measure between two points** (Ctrl+M), then click twice on the model. You get the
straight-line distance and the three axis distances, because "how tall" and "how far across" are
usually the real questions.

You can still orbit the model while measuring — drags belong to the camera, only clicks count. A
click that misses the model is ignored rather than throwing the measurement away, and a third click
starts the next one.

---

## Seeing inside it

**View → Cut it open** (Ctrl+K) slices the *view* through the model so you can see the cavity and
how thick the wall around it came out. Choose which way the cut faces, drag it through the part,
swap to the other half.

This is the only way to check a hollow by looking. The outside of a hollowed box is identical to
the outside of a solid one, so "did that wall come out at 2 mm" was otherwise answerable only by
slicing it or by trusting the number you typed.

It cuts the view and nothing else. Nothing here changes what is exported, sliced or saved, and the
cut travels across the *model* rather than the build plate, so a small part gets a slider that is
all useful rather than one where every position is a hair's breadth from the middle.

---

## Getting the view back

**View → Look into the printer** (Home) puts the camera square on to the machine with the
whole build volume in frame, as if standing in front of it looking in. It is the one to reach for
after panning and zooming somewhere unhelpful.

The other views → Isometric, Top, Front, Right (Ctrl+1 to Ctrl+4) → only *turn* the
camera and leave it where it was, which is what you want when you are lining a part up and not what
you want when you are lost.

It frames the *printer*, not what is in it, so a part parked outside the build volume cannot drag
the view out with it.

---

## Moving it

Two ways, and the buttons are the one to reach for first.

### With buttons (the reliable way)

**Move and turn...** in the CAD tools tab, or **View → Move and turn it...** (Ctrl+Shift+M).

Six direction buttons, a step size, quarter turns about any axis, and the two placements worth
having on a button of their own:

- **Drop it on the bed** puts the lowest point of the part on the plate. It lifts as well as drops,
  so a shape sitting half through the bed — which is where a new one starts, because shapes are
  built centred on the origin — is fixed in one click.
- **Centre it on the plate** slides it over the middle and leaves the height alone.

The arrow keys move it about the plate while the panel has focus, and Page Up and Page Down raise
and lower it. The line at the top says where the part is now, so you can see each nudge land.

### With handles in the viewport

**View → Drag it about** (Ctrl+D) puts handles on the part: an arrow to move it, a ring to
turn it. The status bar shows how far it has gone while you are still dragging.

Worth knowing: the handles start at the *centre* of the part, so the inner half of each arrow is
inside the shape itself. Aim at the outer end of an arrow, away from the geometry. That is why fine
placement is easier with the buttons.

### Either way, it is a step in the tree

A drag is not a special case, and neither is a button. Both end as the same **move** and **rotate**
steps the toolbar emits, so they join the feature tree, read back as a sentence and undo in one
step.

Both need a part with a feature tree — an imported mesh has nowhere to put the steps, and the
app says so rather than letting you move something that springs back on the next rebuild. A twist
about two axes at once is refused rather than rounded to the nearest one, because rounding would
put the part somewhere you did not ask for; turn about one axis at a time.

---

## From a photograph

**File → Make one from a picture** takes a photo or a drawing and produces a mesh. First run is
slow: it loads about ten gigabytes of weights. Needs the generation environment — see
`docs/10-mesh-generation.md`.

**A picture has no scale**, so the model comes out whatever size you ask for, and the app says so
on the finished model. To get the real size instead, put something of known length in the shot — a
ruler, a bank card, a coin — and use **Measure it from the photo...**: drag a line along the
reference, drag another across the subject, and the model comes out the size the real thing is.
The note on the model then says it was *measured*, which means it can be checked with calipers.

A **seed** makes a run repeatable, which is the only way to iterate on a picture rather than gamble
on it. It is not bit-identical — GPU arithmetic is not reproducible — but it gives the same shape.

### Asking for several at once

Set **Make** to more than one shape and the same picture is put to the generator several times. Asked
twice it answers twice differently, and the first answer is rarely the best one — the only way to
tell is to see the others. Each takes about a minute.

They all appear in **Shapes made this session**, under the readiness panel. Click one and it goes
into the viewport at full size, where you can orbit it — which tells you far more about whether it
will print than a thumbnail would. Switching between them is instant; they are all still in memory.

Each row carries its **seed**, so a shape you liked can be asked for again. That list is also your
history: everything generated this session is in it, the newest at the top, up to ten. It is not
saved when you close the app — what survives is what you saved on purpose.

### Rescuing the detail

A generated model carries its fine detail in *colour*, and a slicer cannot see colour. Left alone it
prints as a smooth blob — which is what every consumer AI-3D tool does, and the single biggest
reason their output disappoints.

**Rescue the detail...** appears under the readiness panel for a model that has a texture. It reads
the colour as height and pushes the surface in and out accordingly, so the detail becomes geometry
the slicer can find. The mesh is made denser first, because a feature needs vertices either side of
it to exist at all.

The depth is yours to set, and the dialog says why: **colour is a guess at height**, not a
measurement. A dark patch might be a groove or it might just be dark, and nothing can tell the
difference. Try a number, look at it, try another. Reopening the file gets you back where you
started.

Relief shallower than one layer is quietly deepened — below that the mesh changes and the G-code
comes out identical, which reads as the feature being broken.

---

## Measuring one from several photographs

**File → Measure one from several photographs** is the other photo route, and it is a different
thing from the one above. That one shows a generative model a picture and it *invents* a plausible
object, back included. This one works out where the camera was for each photograph and **measures**
the shape they agree on.

Both give you a mesh. Only one of them is evidence, which is why they are separate menu entries and
why the note on the finished model says which it was.

It needs COLMAP and OpenMVS installed — see `docs/12-photogrammetry.md`.

The capture is the part that decides whether it works:

- Walk right round the subject, a photograph every 10-15 degrees, each overlapping its neighbours
  by well over half. Twenty-odd is where it starts being worth the wait.
- Move yourself, not the subject. A turntable moves the background instead, which is the opposite
  of what the solver needs.
- Plain, shiny and transparent things do not reconstruct at all. No texture to match means nothing
  to match.

Start on **Draft**: it is quick, and it answers the only question that matters on a first run, which
is whether the photographs were good enough.

What comes back has holes where you did not point the camera — **Repair** closes them — and is
usually a third of a million triangles, which **Simplify** fixes. Like a generated model, it has no
scale of its own, so it comes out the size you asked for and says so. Put a ruler in the shot and
use **Measure it from the photo** if the real size matters.

If it says the photographs could not be pieced together, that is the capture rather than the
software. More photographs, moving less between each.

---

## Finding something to start from

**File → Find a model to start from** searches MyMiniFactory and Thingiverse and ranks what comes
back. Say what you want the way you would say it out loud: *an MSI dragon about 6 inches tall*.

You tick a box accepting the licence terms once. The app does not police what you do with a
downloaded model; the tick is you saying you have read them.

MakerWorld has no public API, so it is not searched — download the file and open it.
Printables is link-import. Both are recorded in ADR-0008 with the reasoning.

---

## Printing it

**Place on bed** drops the model onto the plate and centres it. **Repair** closes holes and fixes
inverted faces. **Simplify** cuts the triangle count for a model that is slow to draw.

**Print → Slice** drives Bambu Studio's own command line, so the G-code is exactly what Bambu
Studio would produce. Supports default to tree(auto).

Then the app reads its own output back: how many layers, how tall, how much of the first layer
touches the plate, and **whether anything is printing into thin air**. That last one is the check
worth having — it distinguishes a print that will work from one that will detach halfway up.

**Print → Watch it print** replays the toolpath: the head moves the way the printer would move it,
layer by layer, so you can see the order it builds in and where it collides with what it has
already laid down. It also compares **one plate with an AMS** against **several plates with one
spool**, with the time saved on one side and the purged filament on the other.

**Print → Send it to the printer** puts the sliced job on the printer over your own network — no
Bambu account, no server in the middle. It needs three things from the printer's own network screen:
its address, its serial, and its access code. Put them in **File → Settings**.

Two switches guard it, and both start off:

- **"Really send jobs to this printer"**, in Settings. Until you tick it, ModelPop describes what it
  would send and sends nothing. It goes back to off every time the app starts.
- **Start it now**, asked when you send. The default button is *send the file only* — the file
  lands on the printer and you start it from the printer's screen. Uploading is reversible; a print
  is not, and nothing in ModelPop can stop one once it is going.

Starting a print remotely needs one optional extra (`uv sync --extra printer`). Without it the file
still gets there and the app says to start it from the printer.

**Print → What is the printer doing?** opens a panel that keeps asking: what it is printing, how far
through, how hot. It backs off if the printer stops answering, gives up after a few tries, and stops
by itself when the print ends. It only reads — pausing and cancelling are the printer's own screen,
which is where you would be standing anyway if something had gone wrong.

For a two-colour part, **Split into two colours...** writes the body and the raised lettering as
separate files. Worth knowing before you do: lettering is usually a fraction of a percent of the
part, and each filament change purges about 280 mm³ — so an AMS print of a small logo can waste
more filament than the logo contains. The comparison tells you which way round it falls.

---

## Saving

**Save the project** keeps the feature tree, so you can reopen it and carry on changing it.
**Export the mesh as** writes an STL, 3MF or STEP for a slicer or another program — and loses the
tree, because a mesh has no steps in it.

Save the project if you might want to change it. Export the mesh when you are done.

---

## What it does not do yet

- No sketch constraints. The profile dialog is the useful nine tenths of a sketcher and is honest
  about being it.
- Text cannot be turned straight into a mesh. Describe a part instead, or make a picture first.
- The viewport draws on integrated graphics on a laptop with a discrete card, and nothing in here
  can change that — only your graphics driver's control panel can. It is fast enough that it does
  not matter; **File → Settings** says which card you have got.

See `docs/00-plan.md` for what is built and what is coming.
