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

## Which way is which, and holding the view still

Under the viewport:

**Show X, Y, Z** (on by default) marks the axes on the front-left corner of the plate, in the same
red, green and blue the drag arrows use → so the arrow you are about to pull and the axis it
runs along are obviously the same thing.

**The view turns like a turntable.** Drag sideways to spin the plate round, up and down to raise
and lower your eye. It stays level: there is no third direction to tip it into, so it cannot come
out skewed, and it will not go over the top and leave the model upside down.

**Hold the view: Left/right, Up/down.** Each stops that drag doing anything. Hold **Up/down** to
spin round the plate at one fixed height; hold **Left/right** to raise and lower without the plate
turning under you. Hold both and it will not turn at all.

They are named after the drag rather than the axis it turns about, deliberately. An earlier version
offered X, Y and Z, and five people testing it guessed: "rotate about Z" and "drag left and right"
are the same thing, and nobody should have to translate between them to look at their model.

Panning and zooming are unchanged whatever is held, and the drag handles still win → a drag that
grabbed a handle moves the part and never the view.

---

## How long it took

At the **right-hand end of the status bar** is a timer. It starts when anything begins working,
ticks while it does, and freezes on the final figure when everything goes quiet - so "how long did
that take?" is still answerable after the fact, not only while you are waiting.

It counts every operation, not one kind. Repairing a million triangles is a minute and a half and a
box is a fraction of a second; both now say so. Hover it and it names what it timed.

It is added to the status bar permanently, so the messages that come and go on the left never paint
over it.

---

## Connecting the printer

**File -> Settings** opens on the **Printer** tab. Three fields, all from the printer itself:

On a **P2S**: **Settings -> General -> LAN-Only mode**, and confirm. Turning it on shows the
**address** and the **access code** on that same screen; the **serial** is under
**Settings -> Device**. If the access code reads all zeros, toggle LAN-Only off and on again and it
regenerates. On current firmware you may also need **Developer Mode**, which only appears in that
menu once LAN-Only is on - it is what allows software other than Bambu Studio to talk to the
printer.

That path is the P2S's. It is *not* the X1's, which puts the same settings under a gear icon and
Network, and following the X1 instructions on a P2S finds nothing.

ModelPop is **LAN only** by design (ADR-0011): no Bambu account, no token refresh, no server in the
middle. A printer left in cloud mode will not answer it.

**Leave "Really send jobs to this printer" unticked** until you want a job to actually leave the
machine. Until then ModelPop describes what it would send. It is the only thing the application does
that cannot be undone from inside it.

---

## Which printer it thinks you have

The label at the top left of the viewport names the printer and its build volume, and says where
those figures came from.

**The nozzle comes from the printer**, because it is the one thing that changes without anybody
telling the application - and it decides what counts as a wall too thin to print. Two extrusion
lines: 0.84 mm on a 0.4 mm nozzle, 1.26 mm on a 0.6. The thin-wall warning and *Thicken thin walls*
are both aimed at that figure, so a swapped nozzle nobody mentioned makes both of them wrong.

**The build volume cannot come from the printer.** There is no such field in anything it sends. It
comes from knowing which model it is, and the model is either recognised from a code the printer
announces about itself (`N7` is a P2S, `BL-P001` an X1 Carbon) or set by you in
**File → Settings → The printer → Model**. Left on *Recognise it automatically*, your choice is
whatever the printer says; pick one and it stands whatever the printer says.

The sizes themselves are read from **Bambu Studio's own installed profiles**, which is the same
source the slicer works from, so a model Bambu ships next year needs no change here. Worth knowing:
every P1 and X1 is **250 mm** tall, not 256 - easy to get wrong from memory, and wrong in the
direction that passes a 254 mm model as printable.

**When the printer is off, the last thing it said stands**, and the label says so: *"last seen 3
days ago"*. A reading more than twelve hours old is shown as remembered rather than current, because
a figure from last week presented as live is the one you would act on. With nothing ever heard from
the printer it says *"not confirmed with the printer"* instead.

Changing printer re-measures whatever is open against it - a different nozzle is a different verdict
on the same model.

---

## Pushing and pulling a face

**View -> Push/pull a face** (Ctrl+U) is SketchUp's push/pull. Point at a flat face: it lights up in
amber with an arrow showing which way it will go. Drag it out to add material, drag it in to take
material away. The status bar counts the millimetres as you go, and the result joins the feature
tree, so it undoes in one step and reads back as a sentence.

It moves along the face's **own normal**, not along an axis. That is what makes it read the same
whichever way the part has been turned, and it is the whole difference between push/pull and a move.

A flat surface on a solid is many triangles, so the ones lying in the same plane as the one under
the cursor are gathered and drawn as a single sheet. The two sides of a thin plate are parallel but
are not the same face, and it knows the difference.

**It works on parts with a feature tree**, not on an imported or generated mesh. Pushing a face of a
mesh would mean rebuilding geometry the kernel never made, with nowhere in the tree to record it -
so rather than something that looks live and springs back, it says so.

Push/pull and the drag handles are mutually exclusive: both want the left button, and two tools
fighting over it is worse than either being unavailable. Turning one on turns the other off.

The face is remembered by **a point on it**. Faces have no identity that survives a rebuild - the
tree is rebuilt from nothing every time - so the point you clicked is what finds it again, and on
the next rebuild the face nearest that point is the one that moves. Move something underneath it far
enough and the wrong face is picked; that is the trade every kernel this size makes, and the
alternative is a constraint solver.

---

## Which way up to print it

**Lay it down to print**, on the right-click menu, turns the object to the way up that overhangs
least. Overhanging surface is what needs supports, and supports are what make a print slow, wasteful
and scarred where they are torn off.

It only considers ways the model can actually *stand*. The candidates are the faces of its convex
hull - the positions it would come to rest in if you put it down - and each is scored both on how
much would overhang and on how broad a base it would sit on. That second number is not a detail: on
the dragon, the orientation with the very least overhang balances it upside down on three hundredths
of a square millimetre. It would fall over before the first layer finished.

The turns join the feature tree, so this undoes in one step and reads back as a sentence. It is put
back down on the plate afterwards, because turning happens about the origin and would otherwise
leave it buried or hovering.

It will tell you when there is nothing to gain rather than moving the model for the sake of it. The
dragon is one of those: lying on its side it already overhangs 21%, the best it can steadily manage
is 20%, and it says so and leaves it alone. A T-shaped bracket standing upright goes from 12% to
nothing at all.

---

## When the walls are too thin

The readiness panel warns when the thinnest wall is below what the nozzle can lay down - two
extrusion lines, 0.84 mm on the standard 0.4 mm nozzle. Below that, fine detail either vanishes or
prints as a single fragile line, which is the commonest reason a model that looked crisp on screen
comes off the plate as a blob.

**Thicken thin walls** fixes it, and you can leave it to get on with it. It grows every surface
outwards by a fraction of a millimetre; both faces of a wall move, so the wall gains twice that.
Then it measures again and repeats until the thinnest wall clears the line.

It is done in rounds rather than one calculation because wall thickness is *sampled* - the figure
comes from two thousand rays fired through the surface, so it shifts slightly each time it is taken.

On the dragon: the thinnest wall went **0.65 mm to 0.92 mm** in three rounds, and the model grew
**0.29 mm across - a third of one percent**. Every triangle is the one it started with; nothing is
resampled, so no detail is lost. That is the difference between this and the voxel rebuild the
repair uses as a last resort, which re-derives the surface from a grid and bands it.

It needs a closed model, because rays cannot be fired through a surface with holes in it. If the
button is greyed out, repair it first. If growing it cannot fix the walls - because they need more
than a fraction of a millimetre - it says so and leaves the model alone, and the answer then is to
scale the whole thing up or fit a finer nozzle.

---

## Getting the view back

**View → Look into the printer** (Home) puts the whole build volume back in frame, seen from
where somebody standing at the machine would see it. It is the one to reach for after panning and
zooming somewhere unhelpful.

It is deliberately not dead square on. Straight ahead puts the build plate exactly edge-on, so the
surface everything stands on is an invisible line; a few degrees above and a few round to the left
and the plate reads as a surface and the volume has depth.

The other views → Isometric, Top, Front, Right (Ctrl+1 to Ctrl+4) → only *turn* the
camera and leave it where it was, which is what you want when you are lining a part up and not what
you want when you are lost.

It frames the *printer*, not what is in it, so a part parked outside the build volume cannot drag
the view out with it.

---

## Where new things land

Anything added stands **on** the plate, not through it - a shape, a model from a picture, a file
you opened. Shapes are built centred on the origin, so each is lifted by half its height as it is
added; the tree does not spell that out, because every row saying "at (0, 0, 20)" would be noise.

A cutter is the exception: a drill is positioned to cut something, and moving it to the plate would
put the hole somewhere you did not ask for.

A model that arrives with no scale at all - which is what a picture produces, routinely a single
millimetre across - is given a workable 60 mm and says so in its own name. Set the real size with
**Resize it...** when you know it.

---

## What the tools act on

Everything under **Change it** → round, chamfer, hollow, move, turn, scale, text, mirror, repeat
→ applies to **the whole of the one object you have selected**, and to nothing else on the plate.
There is no sub-selection: rounding rounds that object's edges, not a face or an edge you picked.

The panel is titled with whatever is in hand once there is more than one object, so *Change Sphere*
rather than *Change it*. Click an object in the viewport to pick it up; click empty space to put
everything down, and the operations grey out because there is nothing for them to act on.

**Start a shape** is the exception: a box, cylinder or sphere makes a *new* object. Ticking **Cut**
makes it a hole instead, taken out of the object you have selected.

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

### Turning it

Right-click → **Turn it** has the quarter turns: stand it up, lay it back, tip it left or
right, spin it a quarter or a half. Standing a model up that came out on its back is the commonest
single thing anybody does to one, and it should not need a panel. After a turn the object is put
back on the plate, because a turn is about the world origin and would otherwise leave it under the
bed.

Dragging a ring **snaps to 15 degrees**, which divides into 45, 90 and 180. Hold **Shift** while
dragging for a free angle.

**Move and turn...** has the same turns about any axis, plus a typed angle.

### Resizing it

**Drag a corner.** With the handles on (right-click → **Put handles on it**, or Ctrl+D) every
corner of the object carries a grip. Pull one outwards to grow it, push it in to shrink it; the
corner stays under the pointer, and the object keeps its proportions and its place → it grows
about its own centre rather than running off across the plate.

For when the number is the point, right-click → **Resize it...**. **Half**, **-10%**, **+10%** and **Double** are
proportions of the size it is *now*, which is how resizing by hand actually works; press one twice
and it compounds. There is a box underneath for when the number is the point.

It scales the whole object about its height and keeps its proportions, so a 40 x 20 x 10 block
halved is 20 x 10 x 5.

---

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
