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
    Face,
    Fillet,
    Hollow,
    Move,
    Rotate,
    ScaleTo,
    TextOnSurface,
    command_from,
)
from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from modelpop.application.cad_ports import CadKernel, ScriptResult
    from modelpop.domain.commands import Command, Document, Feature

__all__ = ["Build123dCompiler", "compile_document"]


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


def compile_document(document: Document, part: Part = Part.WHOLE) -> Result[str]:
    """Turn a feature tree into a build123d script.

    Pure: no kernel, no file system, no subprocess. That is what makes the
    generated script assertable in a millisecond test, which matters because
    getting the script wrong is the failure mode with no visible symptom - it
    produces *a* shape, just not the right one.

    Args:
        document: the feature tree.
        part: which piece to build. The default is the whole model; the other
            two separate a lettered model into its two colours.
    """
    features = document.active_features
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

    for index, feature in enumerate(features, start=1):
        command = command_from(feature)
        if command is None:
            unknown.append(feature.name)
            continue

        if part is Part.BODY and _is_decoration(command):
            continue

        fragment = _fragment_for(command, first=not started)
        if fragment is None:
            unknown.append(feature.name)
            continue

        lines.append(f"# {index}. {command.describe()}")
        lines.append(fragment)
        lines.append("")
        started = True

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


def _fragment_for(command: Command, *, first: bool) -> str | None:
    """One feature as a line of build123d.

    ``first`` matters because a creation command starts the solid and anything
    else operates on what is already there. A fillet with nothing to round is a
    mistake worth catching here rather than as a kernel traceback.
    """
    match command:
        case CreateBox():
            return _shape(
                command, f"Box({command.width}, {command.depth}, {command.height})", first=first
            )
        case CreateCylinder():
            return _shape(command, f"Cylinder({command.radius}, {command.height})", first=first)
        case CreateSphere():
            return _shape(command, f"Sphere({command.radius})", first=first)
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
        case TextOnSurface():
            return _text_fragment(command)
        case _:
            return None


def _shape(command: Any, expression: str, *, first: bool) -> str | None:
    """Create a solid, fuse one onto it, or cut one out of it.

    A second primitive is a union rather than a replacement, because the user
    who adds a cylinder to a box means "and also", not "instead". A cut has
    nothing to cut from when it is first, which is refused rather than built as
    an empty model.
    """
    placed = expression
    if command.x or command.y or command.z:
        placed = f"Pos({command.x}, {command.y}, {command.z}) * {expression}"

    if first:
        return None if command.cut else f"result = {placed}"
    return f"result = result {'-' if command.cut else '+'} {placed}"


def _rotation(command: Rotate) -> str:
    """The three Euler angles for a rotation about one axis."""
    angles = {"X": (command.degrees, 0, 0), "Y": (0, command.degrees, 0)}
    x, y, z = angles.get(command.axis, (0, 0, command.degrees))
    return f"{x}, {y}, {z}"


def _scale_fragment(command: ScaleTo) -> str:
    """Scale so the tallest dimension is a stated size.

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
        """Rebuild the tree and return the solid."""
        script = compile_document(document, part)
        if not script.ok:
            return script  # type: ignore[return-value]
        return self._kernel.run(script.unwrap(), timeout_seconds)

    def has_second_colour(self, document: Document) -> bool:
        """Whether this model has raised lettering that could print separately."""
        return any(_is_decoration(command_from(f)) for f in document.active_features)
