"""Turning a feature tree into geometry.

The domain records *intent* - "round the vertical edges by 3 mm" - and knows
nothing about OCCT. This is where intent becomes a build123d script, which the
existing sandboxed kernel then runs.

Going through a script rather than calling build123d directly is deliberate and
costs a subprocess per rebuild:

* the kernel is already sandboxed, timed out and crash-isolated, and a rebuild
  gets all of that for free;
* the script is **readable**, so "show me what this model actually is" is one
  click rather than a feature nobody built;
* the same path serves a user's toolbar click, a model's typed command and a
  replay, which is the whole point of ADR-0001.

A rebuild replays the *whole* tree from nothing. That is what makes the model
parametric: change the first feature's width and everything after it follows.
It is also why the tree is capped - a hundred features is a fraction of a second
per rebuild, and nobody has reached it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from modelpop.application.cad_ports import Part
from modelpop.domain.cad_commands import (
    Chamfer,
    CreateBox,
    CreateCylinder,
    CreateSphere,
    EdgeSelector,
    Extrude,
    Face,
    Fillet,
    Hollow,
    Loft,
    Mirror,
    Move,
    Plane,
    Repeat,
    RepeatAround,
    Revolve,
    Rotate,
    ScaleTo,
    Sweep,
    TextOnSurface,
    command_from,
)
from modelpop.domain.result import Failure, Result, failure, success

if TYPE_CHECKING:
    from modelpop.application.cad_ports import CadKernel, ScriptResult
    from modelpop.domain.commands import Command, Document, Feature

__all__ = ["Build123dCompiler", "compile_document", "compile_scene"]


# A rebuild replays everything, so the cost is linear in the tree. This is far
# above anything a person builds by hand and low enough that a runaway loop of
# model-emitted commands cannot make a rebuild take minutes.
MAX_FEATURES = 200

_PREAMBLE = """\
from build123d import *

# Built from a ModelPop feature tree. Every line below came from one recorded
# feature, in order. Editing this file changes nothing: the tree is the model.

# Raised lettering accumulates here as well as being fused into the body, so the
# same tree can be compiled as one object or as two colours.
_decoration = None
"""

_SCENE_PREAMBLE = """from build123d import *

# Built from a ModelPop feature tree. Each object in the scene is built from
# its own features alone and collected below. Objects are never unioned with
# each other: they are separate things on a plate, and they move separately.
results = {}
"""

_EDGE_FILTERS: dict[EdgeSelector, str] = {
    EdgeSelector.ALL: "result.edges()",
    EdgeSelector.TOP: "result.edges().group_by(Axis.Z)[-1]",
    EdgeSelector.BOTTOM: "result.edges().group_by(Axis.Z)[0]",
    EdgeSelector.VERTICAL: "result.edges().filter_by(Axis.Z)",
    EdgeSelector.HORIZONTAL: (
        "ShapeList([e for e in result.edges() if e not in result.edges().filter_by(Axis.Z)])"
    ),
}

_FACE_PICKERS: dict[Face, str] = {
    Face.TOP: "result.faces().sort_by(Axis.Z)[-1]",
    Face.BOTTOM: "result.faces().sort_by(Axis.Z)[0]",
    Face.FRONT: "result.faces().sort_by(Axis.Y)[0]",
    Face.BACK: "result.faces().sort_by(Axis.Y)[-1]",
    Face.LEFT: "result.faces().sort_by(Axis.X)[0]",
    Face.RIGHT: "result.faces().sort_by(Axis.X)[-1]",
}


def compile_scene(document: Document, part: Part = Part.WHOLE) -> Result[str]:
    """Turn a whole scene - every object in it - into one build123d script.

    One script, and therefore one subprocess, however many objects there are.
    Compiling them separately would be simpler and would multiply the cost of
    every edit by the number of things on the plate: an OCCT rebuild is two
    seconds, and a maker with five objects would wait ten for each nudge.

    Each object is built from its own features alone and collected into
    ``results``, keyed by body id. Nothing is unioned across objects - that
    union was the whole reason two shapes could not be moved independently.
    """
    bodies = document.body_ids
    if not bodies:
        return failure("There is nothing to build", "The scene has no objects yet.")

    lines: list[str] = [_SCENE_PREAMBLE]
    built: list[str] = []
    refusals: list[str] = []

    for body in bodies:
        one = compile_document(document, part, body=body)
        if isinstance(one, Failure):
            refusals.append(f"{document.label_for(body)}: {one.reason}")
            continue
        # Emitted at module level rather than wrapped in a function: the
        # preamble each object carries does `from build123d import *`, and a
        # star import inside a function is a syntax error. Each object's first
        # feature assigns `result` outright, so nothing leaks between them.
        lines.append(f"# ======== {document.label_for(body)} ({body}) ========")
        lines.append(one.unwrap())
        lines.append(f"results[{body!r}] = result")
        lines.append("")
        built.append(body)

    if not built:
        return failure(
            "Nothing in the scene could be rebuilt",
            "; ".join(refusals) or "every object was empty.",
        )

    # The first object also lands in `result`, so anything still expecting a
    # single solid - the STEP export, the older worker path - keeps working.
    lines.append(f"result = results[{built[0]!r}]")
    return success("\n".join(lines))


def compile_document(
    document: Document, part: Part = Part.WHOLE, body: str | None = None
) -> Result[str]:
    """Turn a feature tree into a build123d script.

    Pure: no kernel, no file system, no subprocess. That is what makes the
    generated script assertable in a millisecond test, which matters because
    getting the script wrong is the failure mode with no visible symptom - it
    produces *a* shape, just not the right one.

    Args:
        document: the feature tree.
        part: which piece to build. The default is the whole model; the other
            two separate a lettered model into its two colours.
        body: build only this object's features. ``None`` builds every feature
            in the document, which is what a single-object scene amounts to.
    """
    features = document.features_for(body) if body is not None else document.active_features
    if not features:
        return failure("There is nothing to build", "The model has no features yet.")
    if len(features) > MAX_FEATURES:
        return failure(
            "That model has too many features to rebuild",
            f"{len(features)} features; the limit is {MAX_FEATURES}.",
        )

    impossible = _known_to_fail(features)
    if impossible is not None:
        return impossible

    lines: list[str] = [_PREAMBLE]
    unknown: list[str] = []
    started = False
    repeatable: Command | None = None

    for index, feature in enumerate(features, start=1):
        command = command_from(feature)
        if command is None:
            unknown.append(feature.name)
            continue

        if part is Part.BODY and _is_decoration(command):
            continue

        if isinstance(command, Repeat | RepeatAround):
            fragment = _pattern_fragment(command, repeatable)
        else:
            fragment = _fragment_for(command, first=not started)

        if fragment is None:
            unknown.append(feature.name)
            continue

        lines.append(f"# {index}. {command.describe()}")
        lines.append(fragment)
        lines.append("")
        started = True

        # A pattern repeats the shape before it, so the tree has to remember
        # what that was. Only shapes qualify: repeating a fillet means nothing.
        if _is_a_shape(command):
            repeatable = command

    if not started:
        return failure(
            "None of this model's features can be rebuilt",
            f"Unrecognised: {', '.join(unknown)}." if unknown else "The tree is empty.",
        )

    if part is Part.DECORATION:
        if not any(_is_decoration(command_from(f)) for f in features):
            return failure(
                "There is nothing to print in a second colour",
                "Add raised text to the model first.",
            )
        lines.append("# Only the raised lettering, for the second filament.")
        lines.append("result = _decoration")
        lines.append("")

    if unknown:
        # Said out loud rather than skipped silently. A document saved by a
        # newer build must open here, but the user has to know it is not whole.
        lines.append(f"# Skipped, not understood by this version: {', '.join(unknown)}")

    return success("\n".join(lines))


def _is_decoration(command: Command | None) -> bool:
    """Whether a feature is lettering that stands proud of the body.

    Engraved text is *not* decoration: it is a hole in the body, and there is no
    second solid to print in another colour.
    """
    return isinstance(command, TextOnSurface) and command.raised


def _known_to_fail(features: tuple[Feature, ...]) -> Result[str] | None:
    """Catch combinations OCCT refuses, before it refuses them.

    OCCT reports these as ``Standard_ConstructionError`` with no further detail,
    which tells the user nothing they can act on. Each entry here was found by
    measurement, and each says what to change.
    """
    radii: list[float] = []
    for feature in features:
        command = command_from(feature)
        if isinstance(command, Sweep | Loft) and command.problem is not None:
            # These two carry their own diagnosis, because they have several
            # ways to be wrong and "could not build that shape" names none of
            # them. Reported here rather than compiled to nothing, which would
            # read as a feature this version does not understand.
            return failure(f"{command.describe()} cannot be built", command.problem)
        if isinstance(command, Fillet):
            radii.append(round(command.radius, 6))
        elif isinstance(command, Hollow):
            wall = round(command.wall_thickness, 6)
            # Offsetting inward by exactly a fillet radius collapses the inner
            # fillet to zero radius. Measured: a 2.0 mm wall on a 2.0 mm fillet
            # fails, while 1.9 and 2.5 both succeed.
            if wall in radii:
                return failure(
                    f"A {wall:g} mm wall cannot be hollowed out of a {wall:g} mm rounded edge",
                    f"The wall is exactly the fillet radius, which leaves no inner "
                    f"corner. Try {wall - 0.2:g} mm or {wall + 0.5:g} mm.",
                )
    return None


def _fragment_for(command: Command, *, first: bool, copy: str = "") -> str | None:
    """One feature as a line of build123d.

    ``first`` matters because a creation command starts the solid and anything
    else operates on what is already there. A fillet with nothing to round is a
    mistake worth catching here rather than as a kernel traceback.

    ``copy`` is a build123d transform applied to a shape before it is combined,
    which is how a pattern re-emits the shape before it without that shape
    needing to know it is being copied.
    """
    match command:
        case CreateBox():
            return _shape(
                command,
                f"Box({command.width}, {command.depth}, {command.height})",
                first=first,
                copy=copy,
            )
        case CreateCylinder():
            return _shape(
                command, f"Cylinder({command.radius}, {command.height})", first=first, copy=copy
            )
        case CreateSphere():
            return _shape(command, f"Sphere({command.radius})", first=first, copy=copy)
        case Extrude():
            return _extrude_fragment(command, first=first, copy=copy)
        case Revolve():
            return _revolve_fragment(command, first=first, copy=copy)
        case Sweep():
            return _sweep_fragment(command, first=first, copy=copy)
        case Loft():
            return _loft_fragment(command, first=first, copy=copy)
        case _ if first:
            return None  # nothing to operate on yet

        case Fillet():
            selected = _EDGE_FILTERS[command.edges]
            return f"result = fillet({selected}, radius={command.radius})"
        case Chamfer():
            selected = _EDGE_FILTERS[command.edges]
            return f"result = chamfer({selected}, length={command.distance})"
        case Hollow():
            openings = (
                f"[{_FACE_PICKERS[command.opening]}]" if command.opening is not None else "[]"
            )
            return f"result = offset(result, amount=-{command.wall_thickness}, openings={openings})"
        case Move():
            return f"result = Pos({command.dx}, {command.dy}, {command.dz}) * result"
        case Rotate():
            return f"result = Rot({_rotation(command)}) * result"
        case ScaleTo():
            return _scale_fragment(command)
        case Mirror():
            return _mirror_fragment(command)
        case TextOnSurface():
            return _text_fragment(command)
        case _:
            return None


_PLANES = {Plane.XY: "Plane.XY", Plane.XZ: "Plane.XZ", Plane.YZ: "Plane.YZ"}


def _centre_of(points: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    """The middle of an outline's bounding box.

    The bounding box rather than the centroid: a user reading "60 by 40 mm" off
    the preview expects the part to straddle the origin by 30 and 20, and a
    centroid puts an L-shape somewhere neither obvious nor useful.
    """
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def _extrude_fragment(command: Extrude, *, first: bool, copy: str = "") -> str | None:
    """An outline given thickness.

    Refused rather than built when the outline cannot enclose an area. Two
    points are a line, and OCCT's complaint about extruding one names neither
    the feature nor the problem.

    The result is **centred on the origin**, in the plane and through the
    thickness, because every other shape in the vocabulary is. Measured the
    other way round first: an outline left where it was drawn and grown upwards
    from the plane cut only half way through a 10 mm plate, because the plate
    is centred and straddles the plane. The corners a user types are therefore
    read as a shape, not as a position - ``move`` is what places it.
    """
    if not command.is_closed_enough:
        return None

    centre_x, centre_y = _centre_of(command.points)
    points = ", ".join(f"({x - centre_x:g}, {y - centre_y:g})" for x, y in command.points)
    plane = _PLANES[command.plane]
    lines = [
        f"_outline = Polyline([{points}], close=True)",
        f"_profile = make_face({plane} * _outline)",
        f"_solid = extrude(_profile, amount={command.height / 2}, both=True)",
    ]

    if copy:
        lines.append(f"_solid = {copy} * _solid")

    if first:
        if command.cut:
            return None  # nothing to cut from yet
        lines.append("result = _solid")
    else:
        lines.append(f"result = result {'-' if command.cut else '+'} _solid")

    return "\n".join(lines)


def _revolve_fragment(command: Revolve, *, first: bool, copy: str = "") -> str | None:
    """A profile spun round the upright axis.

    Centred through its height, like everything else here, but *not* across
    its width: the first number in each corner is a radius, and moving the
    profile sideways would change the shape rather than where it sits.
    """
    if not command.is_closed_enough:
        return None

    heights = [height for _, height in command.points]
    middle = (min(heights) + max(heights)) / 2
    points = ", ".join(f"({radius:g}, {height - middle:g})" for radius, height in command.points)
    lines = [
        f"_outline = Polyline([{points}], close=True)",
        "_profile = make_face(Plane.XZ * _outline)",
        f"_solid = revolve(_profile, axis=Axis.Z, revolution_arc={command.degrees:g})",
    ]

    if copy:
        lines.append(f"_solid = {copy} * _solid")

    if first:
        if command.cut:
            return None  # nothing to cut from yet
        lines.append("result = _solid")
    else:
        lines.append(f"result = result {'-' if command.cut else '+'} _solid")

    return "\n".join(lines)


# Recentring by the *built* bounding box rather than by arithmetic on the
# points. A sweep's solid reaches wider than its path and a loft can bulge
# between its sections, so neither centre can be worked out before the kernel
# has built the thing - and "every shape is centred on the origin" is what
# mirror and the patterns rely on.
_CENTRE_ON_ORIGIN = (
    "_middle = _solid.bounding_box().center()\n"
    "_solid = Pos(-_middle.X, -_middle.Y, -_middle.Z) * _solid"
)


def _sweep_fragment(command: Sweep, *, first: bool, copy: str = "") -> str | None:
    """An outline pushed along a path.

    Two things here are not obvious and both were measured rather than assumed.

    The section is placed **square to the first leg of the path**, not on a
    named plane. A section lying in the plane its path travels in sweeps to a
    volume of exactly zero and OCCT reports success, so letting the user choose
    the plane offers them a silent way to build nothing.

    The path's corners are **rounded before it is swept**. A mitred corner
    folds through itself: a 10 x 10 section along a right-angled 70 mm path
    measured 3200 mm3 against the 7000 it should be, again with no error. Any
    bend radius at all makes it exact, which is why the domain enforces a floor
    rather than offering a choice.
    """
    if command.problem is not None:
        return None

    centre_x, centre_y = _centre_of(command.points)
    outline = ", ".join(f"({x - centre_x:g}, {y - centre_y:g})" for x, y in command.points)
    start = command.path[0]
    heading = command.path[1]
    route = ", ".join(f"({x:g}, {y:g}, {z:g})" for x, y, z in command.path)

    lines = [
        f"_outline = Polyline([{outline}], close=True)",
        f"_start = Vector({start[0]:g}, {start[1]:g}, {start[2]:g})",
        f"_heading = Vector({heading[0]:g}, {heading[1]:g}, {heading[2]:g}) - _start",
        "_section = make_face(Plane(origin=_start, z_dir=_heading) * _outline)",
        f"_path = Polyline([{route}])",
    ]
    if command.turns:
        lines.append(f"_path = fillet(_path.vertices(), radius={command.bend_radius:g})")
    lines.append("_solid = sweep(_section, path=_path)")
    lines.append(_CENTRE_ON_ORIGIN)

    return _finish_solid(lines, first=first, cut=command.cut, copy=copy)


def _loft_fragment(command: Loft, *, first: bool, copy: str = "") -> str | None:
    """Outlines at different heights, blended into one solid.

    The sections keep the x and y they were drawn with: a loft that leans, or
    one whose top is off to one side, is a real shape somebody meant. Only the
    finished solid is recentred.
    """
    if command.problem is not None:
        return None

    faces = [
        f"    make_face(Plane.XY.offset({section.height:g}) * Polyline(["
        + ", ".join(f"({x:g}, {y:g})" for x, y in section.points)
        + "], close=True)),"
        for section in command.sections
    ]
    lines = ["_sections = [", *faces, "]", "_solid = loft(_sections)", _CENTRE_ON_ORIGIN]

    return _finish_solid(lines, first=first, cut=command.cut, copy=copy)


def _finish_solid(lines: list[str], *, first: bool, cut: bool, copy: str) -> str | None:
    """Place a built ``_solid``, then add it to the model or cut it out.

    Shared by the two operations that build into ``_solid`` and then have to
    join the tree the same way every other shape does.
    """
    if copy:
        lines.append(f"_solid = {copy} * _solid")
    if first:
        if cut:
            return None  # nothing to cut from yet
        lines.append("result = _solid")
    else:
        lines.append(f"result = result {'-' if cut else '+'} _solid")
    return "\n".join(lines)


def _is_a_shape(command: Command) -> bool:
    """Whether a feature adds or removes material in its own right.

    Only these can be patterned. Repeating a fillet or a hollow is meaningless,
    and quietly repeating the shape before it instead would produce a model
    that is not what anyone asked for.
    """
    return isinstance(
        command, CreateBox | CreateCylinder | CreateSphere | Extrude | Revolve | Sweep | Loft
    )


def _pattern_fragment(command: Repeat | RepeatAround, shape: Command | None) -> str | None:
    """Copies of the shape before this one, laid out.

    Re-emitting the earlier fragment rather than copying the built solid keeps
    a cut a cut: patterning a drilled hole has to remove six holes, and adding
    a copy of the whole result would fill six plugs in instead. It also means
    each copy goes through exactly the same code path as the original, so a
    shape cannot pattern differently from how it builds.
    """
    if shape is None:
        return None

    lines: list[str] = []
    for step in range(1, command.times):
        fragment = _fragment_for(shape, first=False, copy=_copy_transform(command, step))
        if fragment is None:
            return None
        lines.append(fragment)

    # One copy is the shape itself, already in the tree. Said out loud rather
    # than emitted as nothing, which reads as a feature that failed.
    return "\n".join(lines) if lines else "# one copy is the shape itself; nothing to add"


def _copy_transform(command: Repeat | RepeatAround, step: int) -> str:
    """Where the nth copy of a patterned shape goes."""
    if isinstance(command, Repeat):
        return f"Pos({command.dx * step:g}, {command.dy * step:g}, {command.dz * step:g})"
    angle = command.step_degrees * step
    angles = {"X": (angle, 0.0, 0.0), "Y": (0.0, angle, 0.0)}
    x, y, z = angles.get(command.axis, (0.0, 0.0, angle))
    return f"Rot({x:g}, {y:g}, {z:g})"


def _mirror_fragment(command: Mirror) -> str:
    """The part reflected, with or without the original.

    The plane passes through the origin, which is where every shape here is
    centred, so a half modelled about the origin meets its reflection exactly.
    """
    reflected = f"mirror(result, about={_PLANES[command.plane]})"
    return f"result = result + {reflected}" if command.keep_original else f"result = {reflected}"


def _shape(command: Any, expression: str, *, first: bool, copy: str = "") -> str | None:
    """Create a solid, fuse one onto it, or cut one out of it.

    A second primitive is a union rather than a replacement, because the user
    who adds a cylinder to a box means "and also", not "instead". A cut has
    nothing to cut from when it is first, which is refused rather than built as
    an empty model.
    """
    placed = expression
    if command.x or command.y or command.z:
        placed = f"Pos({command.x}, {command.y}, {command.z}) * {expression}"
    if copy:
        placed = f"{copy} * ({placed})"

    if first:
        return None if command.cut else f"result = {placed}"
    return f"result = result {'-' if command.cut else '+'} {placed}"


def _rotation(command: Rotate) -> str:
    """The three Euler angles for a rotation about one axis."""
    angles = {"X": (command.degrees, 0, 0), "Y": (0, command.degrees, 0)}
    x, y, z = angles.get(command.axis, (0, 0, command.degrees))
    return f"{x}, {y}, {z}"


def _scale_fragment(command: ScaleTo) -> str:
    """Scale uniformly so the part stands a stated height.

    Computed at rebuild time from the solid's actual height rather than baked
    in, so scaling to six inches still gives six inches after an earlier
    feature changed the shape. That is the difference between a parametric
    model and a recording of one.
    """
    target = command.parameters["height_mm"]
    return (
        f"_target = {target}\n"
        "_bbox = result.bounding_box()\n"
        "_factor = _target / max(_bbox.size.Z, 1e-6)\n"
        "result = scale(result, by=_factor)"
    )


def _text_fragment(command: TextOnSurface) -> str:
    """Emboss or engrave text on a named face.

    The text is placed on the face's own plane and extruded into or out of it.
    Escaped rather than interpolated raw: the string came from a prompt, and a
    quote in it would otherwise end the literal and start executing.
    """
    safe = repr(command.text)
    picker = _FACE_PICKERS[command.face]
    sign = "" if command.raised else "-"
    return (
        f"_face = {picker}\n"
        "_plane = Plane(_face)\n"
        "_centred = (Align.CENTER, Align.CENTER)\n"
        f"_glyphs = _plane * Text({safe}, font_size={command.size}, align=_centred)\n"
        f"_relief = extrude(_glyphs, amount={sign}{command.depth})\n"
        + (
            "_decoration = _relief if _decoration is None else _decoration + _relief\n"
            if command.raised
            else ""
        )
        + f"result = result {'+' if command.raised else '-'} _relief"
    )


class Build123dCompiler:
    """Rebuilds a feature tree into geometry through the sandboxed kernel.

    Satisfies the ``FeatureCompiler`` port. Holds no state: a rebuild is a pure
    function of the document, which is what lets the UI throw the result away
    and ask again without worrying about what it left behind.
    """

    def __init__(self, kernel: CadKernel) -> None:
        """Wire the compiler to a kernel."""
        self._kernel = kernel

    def is_available(self) -> bool:
        """Whether a rebuild can run right now."""
        return self._kernel.is_available()

    def script_for(self, document: Document, part: Part = Part.WHOLE) -> Result[str]:
        """The build123d source this document compiles to.

        Exposed so the user can read what their model actually is, and so a
        test can assert on the script without paying for a subprocess.
        """
        return compile_document(document, part)

    def build(
        self,
        document: Document,
        timeout_seconds: float = 60.0,
        part: Part = Part.WHOLE,
    ) -> Result[ScriptResult]:
        """Rebuild every object in the scene and return them.

        One script and therefore one subprocess, however many objects there
        are - see ``compile_scene``.
        """
        script = compile_scene(document, part)
        if not script.ok:
            return script  # type: ignore[return-value]
        return self._kernel.run(script.unwrap(), timeout_seconds)

    def has_second_colour(self, document: Document) -> bool:
        """Whether this model has raised lettering that could print separately."""
        return any(_is_decoration(command_from(f)) for f in document.active_features)
