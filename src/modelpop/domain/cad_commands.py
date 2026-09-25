"""The CAD vocabulary: every way a model may be changed.

This is the other half of ADR-0001. The command bus says *how* a change is
applied and recorded; this says *what* changes exist. Three actors emit these -
the user through the toolbar, a language model responding to a prompt, and
replay - and all three are confined to exactly this list.

That confinement is the point. A model that can only emit ``Fillet(radius=2.0,
edges=TOP)`` cannot emit anything else, which is a security boundary as much as
a quality one. It is also why every command validates and **clamps** its
parameters at construction: a value arriving from a language model is untrusted
data, and a fillet radius of ten million should become a refusal or a sane
number here, not an OCCT crash three layers down.

Nothing in this module knows what a kernel is. A command carries intent;
turning intent into geometry is the job of a compiler in the adapter layer,
which is what keeps the domain free of OCCT.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from pathlib import Path
from typing import Any

from modelpop.domain.commands import Command, Feature
from modelpop.domain.units import Length

__all__ = [
    "MAX_COPIES",
    "MAX_OUTLINE_POINTS",
    "MAX_PATH_POINTS",
    "MAX_SECTIONS",
    "MIN_BEND_MM",
    "MIN_OUTLINE_POINTS",
    "MIN_PATH_POINTS",
    "Chamfer",
    "CreateBox",
    "CreateCylinder",
    "CreateSphere",
    "EdgeSelector",
    "Extrude",
    "Face",
    "Fillet",
    "Hollow",
    "Loft",
    "Mirror",
    "Move",
    "PlaceMesh",
    "Plane",
    "PushPull",
    "Repeat",
    "RepeatAround",
    "Revolve",
    "Rotate",
    "ScaleTo",
    "Section",
    "Sweep",
    "TextOnSurface",
    "command_from",
    "known_commands",
    "widest_bend",
]

# Nothing on a P2S can be bigger than its build volume, and a value larger than
# this is a mistake or a hallucination rather than a request. Clamping rather
# than refusing keeps a near-miss usable; the UI shows what was applied.
MAX_MM = 1000.0
MIN_MM = 0.01

# A fillet larger than the feature it rounds fails inside OCCT with an error
# nobody can act on. This bound is generous and still catches the absurd.
MAX_RADIUS_MM = 200.0

MAX_TEXT = 80


# Below this a push or pull is a slip of the hand, not an instruction. A
# hundredth of a millimetre is well under a layer and under anything a nozzle
# could lay down.
LEAST_PUSH_MM = 0.01


def _clamp(value: float, low: float = MIN_MM, high: float = MAX_MM) -> float:
    """Force a number into a range a kernel can survive.

    Not defensive programming for its own sake: these numbers arrive from a
    language model, and an unclamped one reaches OCCT as a crash rather than a
    message.
    """
    if value != value:  # NaN, which compares unequal to itself
        return low
    return max(low, min(high, float(value)))


def _clamp_position(command: Any) -> None:
    """Keep a shape's placement inside anything a printer could hold.

    A translation may be negative, so this is a symmetric clamp rather than the
    positive one dimensions get.
    """
    for axis in ("x", "y", "z"):
        object.__setattr__(command, axis, _clamp(getattr(command, axis), -MAX_MM, MAX_MM))


def _position_of(command: Any) -> dict[str, Any]:
    """The placement fields, for the recorded feature."""
    return {"x": command.x, "y": command.y, "z": command.z, "cut": command.cut}


def _verb(command: Any) -> str:
    """Whether this shape is added or cut, as a word for the feature tree."""
    return "Cut" if command.cut else "Add"


def _where(command: Any, standing: float = 0.0) -> str:
    """A phrase naming where a shape sits, or nothing when it is unremarkable.

    Two positions say nothing worth reading: centred on the origin, and simply
    standing on the build plate. The second is where every new shape is put -
    a shape centred on the origin has half of itself below the bed - and
    spelling out "at (0, 0, 20)" on every row would be noise on the one line
    that is meant to read like a sentence.
    """
    if command.x == 0 and command.y == 0:
        if command.z == 0:
            return ""
        if standing and abs(command.z - standing / 2) < 1e-9:
            return ""
    return f" at ({command.x:g}, {command.y:g}, {command.z:g})"


# Three points is the fewest that can enclose an area. Below that there is
# nothing to extrude.
MIN_OUTLINE_POINTS = 3

# An outline is drawn or described, not generated. Beyond this it is a mistake
# or a model that has run away, and OCCT will be slow about it either way.
MAX_OUTLINE_POINTS = 500


def _tidy_outline(points: Any) -> tuple[tuple[float, float], ...]:
    """Clean an outline into something a kernel can use.

    Clamped, finite, capped in length, and with consecutive duplicates removed -
    a repeated point makes a zero-length edge, which OCCT reports as a failure
    with no hint about which point caused it.
    """
    cleaned: list[tuple[float, float]] = []
    for point in list(points)[:MAX_OUTLINE_POINTS]:
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        spot = (_clamp(x, -MAX_MM, MAX_MM), _clamp(y, -MAX_MM, MAX_MM))
        if cleaned and _is_same_spot(cleaned[-1], spot):
            continue
        cleaned.append(spot)

    # A closing point that repeats the first is redundant: the outline closes
    # itself, and leaving it in makes another zero-length edge.
    if len(cleaned) > 1 and _is_same_spot(cleaned[0], cleaned[-1]):
        cleaned.pop()

    return tuple(cleaned)


def _is_same_spot(one: tuple[float, float], two: tuple[float, float]) -> bool:
    """Whether two points are close enough to make a zero-length edge."""
    return abs(one[0] - two[0]) < MIN_MM and abs(one[1] - two[1]) < MIN_MM


class Plane(Enum):
    """Which face of the world an outline is drawn on."""

    XY = "floor"
    """Flat on the bed, extruded upwards. What most parts want."""

    XZ = "front"
    YZ = "side"

    @property
    def describe(self) -> str:
        """A phrase for the dialog."""
        return {
            Plane.XY: "flat on the bed, growing upwards",
            Plane.XZ: "standing up, facing you",
            Plane.YZ: "standing up, edge on",
        }[self]


# A pattern is a boolean operation per copy. This is far more than anyone lays
# out by hand and low enough that a hallucinated count cannot turn one command
# into a rebuild that never returns.
MAX_COPIES = 100


def _clamp_count(value: Any) -> int:
    """A repeat count that is a whole number and worth doing."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 2
    return max(1, min(count, MAX_COPIES))


def _clamp_arc(value: Any) -> float:
    """How far round to sweep. A full turn is the default and the maximum."""
    try:
        degrees = float(value)
    except (TypeError, ValueError):
        return 360.0
    return min(max(degrees, 1.0), 360.0)


def _against_the_axis(
    points: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    """Push a profile out of the axis it will spin around.

    A radius below zero is the same material swept twice, which OCCT reports
    as a self-intersection. Shifting the whole profile keeps its shape, which
    is what somebody who drew it in the wrong corner actually wanted; clamping
    each corner separately would flatten one side of it instead.
    """
    if not points:
        return points
    inside = min(radius for radius, _ in points)
    if inside >= 0:
        return points
    return tuple((radius - inside, height) for radius, height in points)


class EdgeSelector(Enum):
    """Which edges an operation applies to.

    A named set rather than indices. Edge numbering is not stable across a
    rebuild, so a feature that said "edge 7" would round a different edge after
    the operation before it changed - the classic parametric CAD failure.
    """

    ALL = "all"
    TOP = "top"
    BOTTOM = "bottom"
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"

    @property
    def describe(self) -> str:
        """A phrase for the feature tree."""
        return {
            EdgeSelector.ALL: "all edges",
            EdgeSelector.TOP: "the top edges",
            EdgeSelector.BOTTOM: "the bottom edges",
            EdgeSelector.VERTICAL: "the vertical edges",
            EdgeSelector.HORIZONTAL: "the horizontal edges",
        }[self]


class Face(Enum):
    """A named face, for operations that need one."""

    TOP = "top"
    BOTTOM = "bottom"
    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class CreateBox(Command):
    """A rectangular block, added to the part or cut out of it."""

    width: float
    depth: float
    height: float
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    cut: bool = False
    """Subtract this shape instead of adding it. A pocket, a slot, a notch."""

    def __post_init__(self) -> None:
        """Clamp every dimension into something a kernel can build."""
        object.__setattr__(self, "width", _clamp(self.width))
        object.__setattr__(self, "depth", _clamp(self.depth))
        object.__setattr__(self, "height", _clamp(self.height))
        _clamp_position(self)

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "create-box"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {
            "width": self.width,
            "depth": self.depth,
            "height": self.height,
            **_position_of(self),
        }

    def describe(self) -> str:
        """A line for the feature tree."""
        shape = f"{self.width:g} x {self.depth:g} x {self.height:g} mm box"
        return f"{_verb(self)} a {shape}{_where(self, self.height)}"


@dataclass(frozen=True, slots=True)
class CreateCylinder(Command):
    """A cylinder, added to the part or cut out of it.

    Cutting one is how a hole is made, which is the operation printed parts need
    more than any other.
    """

    radius: float
    height: float
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    cut: bool = False

    def __post_init__(self) -> None:
        """Clamp the dimensions."""
        object.__setattr__(self, "radius", _clamp(self.radius))
        object.__setattr__(self, "height", _clamp(self.height))
        _clamp_position(self)

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "create-cylinder"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"radius": self.radius, "height": self.height, **_position_of(self)}

    def describe(self) -> str:
        """A line for the feature tree."""
        if self.cut:
            return f"Drill a {self.radius * 2:g} mm hole{_where(self)}"
        shape = f"{self.radius:g} mm radius cylinder, {self.height:g} mm tall"
        return f"Add a {shape}{_where(self, self.height)}"


@dataclass(frozen=True, slots=True)
class CreateSphere(Command):
    """A sphere, added to the part or cut out of it."""

    radius: float
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    cut: bool = False

    def __post_init__(self) -> None:
        """Clamp the radius."""
        object.__setattr__(self, "radius", _clamp(self.radius))
        _clamp_position(self)

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "create-sphere"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"radius": self.radius, **_position_of(self)}

    def describe(self) -> str:
        """A line for the feature tree."""
        return f"{_verb(self)} a {self.radius:g} mm radius sphere{_where(self, self.radius * 2)}"


@dataclass(frozen=True, slots=True)
class Fillet(Command):
    """Round edges."""

    radius: float
    edges: EdgeSelector = EdgeSelector.ALL

    def __post_init__(self) -> None:
        """Clamp the radius to something OCCT will attempt."""
        object.__setattr__(self, "radius", _clamp(self.radius, MIN_MM, MAX_RADIUS_MM))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "fillet"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"radius": self.radius, "edges": self.edges.value}

    def describe(self) -> str:
        """A line for the feature tree."""
        return f"Round {self.edges.describe} by {self.radius:g} mm"


@dataclass(frozen=True, slots=True)
class Chamfer(Command):
    """Cut a flat bevel on edges."""

    distance: float
    edges: EdgeSelector = EdgeSelector.ALL

    def __post_init__(self) -> None:
        """Clamp the distance."""
        object.__setattr__(self, "distance", _clamp(self.distance, MIN_MM, MAX_RADIUS_MM))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "chamfer"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"distance": self.distance, "edges": self.edges.value}

    def describe(self) -> str:
        """A line for the feature tree."""
        return f"Chamfer {self.edges.describe} by {self.distance:g} mm"


@dataclass(frozen=True, slots=True)
class Hollow(Command):
    """Hollow the part out, leaving a wall.

    "A hollow core", which is one of the two things Rob asked for by name. Also
    the single most effective way to cut print time and filament on a large
    model, so it earns its place in the toolbar rather than a menu.
    """

    wall_thickness: float
    opening: Face | None = None
    """Which face to leave open, so the inside can drain. ``None`` seals it."""

    def __post_init__(self) -> None:
        """Clamp the wall to something printable.

        Below about half a nozzle width the wall does not exist in the slice, so
        a hollow with a 0.05 mm wall is a hollow with no wall.
        """
        object.__setattr__(self, "wall_thickness", _clamp(self.wall_thickness, 0.4, 50.0))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "hollow"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {
            "wall_thickness": self.wall_thickness,
            "opening": self.opening.value if self.opening else None,
        }

    def describe(self) -> str:
        """A line for the feature tree."""
        where = f", open at the {self.opening.value}" if self.opening else ""
        return f"Hollow to a {self.wall_thickness:g} mm wall{where}"


@dataclass(frozen=True, slots=True)
class Move(Command):
    """Shift the part."""

    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

    def __post_init__(self) -> None:
        """Clamp each offset; a translation may be negative."""
        for axis in ("dx", "dy", "dz"):
            object.__setattr__(self, axis, _clamp(getattr(self, axis), -MAX_MM, MAX_MM))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "move"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"dx": self.dx, "dy": self.dy, "dz": self.dz}

    def describe(self) -> str:
        """A line for the feature tree."""
        return f"Move by ({self.dx:g}, {self.dy:g}, {self.dz:g}) mm"


@dataclass(frozen=True, slots=True)
class Rotate(Command):
    """Turn the part about an axis, in degrees."""

    degrees: float
    axis: str = "Z"

    def __post_init__(self) -> None:
        """Normalise the angle and the axis name."""
        object.__setattr__(self, "degrees", float(self.degrees) % 360.0)
        letter = str(self.axis).upper()[:1]
        object.__setattr__(self, "axis", letter if letter in "XYZ" else "Z")

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "rotate"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"degrees": self.degrees, "axis": self.axis}

    def describe(self) -> str:
        """A line for the feature tree."""
        return f"Rotate {self.degrees:g} degrees about {self.axis}"


@dataclass(frozen=True, slots=True)
class PlaceMesh(Command):
    """Put a mesh that arrived whole into the scene as an object.

    A model opened from a file, made from a picture or reconstructed from
    photographs has no steps behind it - it is geometry, not intent. It is
    still a *thing on the plate*, and everything a thing on the plate can do
    - be picked up, moved, turned, resized, copied, deleted - has to work on
    it exactly as it works on a built part.

    So it enters the tree as one step, and the steps after it are the ordinary
    ``Move``, ``Rotate`` and ``ScaleTo``. The document still holds no geometry
    (ADR-0001): this records *where the mesh is*, and rebuilding loads it and
    replays the transforms over it. Which is why it is fast - measured at 40 ms
    to load 82,000 triangles, against two seconds for an OCCT rebuild - and why
    it never goes near the kernel.
    """

    source: str
    """The file the geometry lives in."""

    note: str = ""
    """Where it came from, in the words the user would use."""

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "place-mesh"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"source": str(self.source), "note": str(self.note)}

    def describe(self) -> str:
        """A line for the feature tree."""
        return self.note or f"Place {Path(self.source).name}"


@dataclass(frozen=True, slots=True)
class ScaleTo(Command):
    """Resize the part uniformly so it stands a stated height.

    Scaling *to a size* rather than *by a factor*, because that is how people
    ask: "about six inches tall", not "times 3.7".
    """

    height: Length

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "scale-to"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"height_mm": _clamp(self.height.millimetres)}

    def describe(self) -> str:
        """A line for the feature tree."""
        return f"Scale to {self.height.format()} tall"


@dataclass(frozen=True, slots=True)
class TextOnSurface(Command):
    """Emboss or deboss text on a face.

    The other thing Rob asked for by name - "MSI in grey across his front" - and
    one of the most-wanted edits for a printed model. Raised text in a second
    colour is a single filament change on an AMS.
    """

    text: str
    face: Face = Face.FRONT
    size: float = 10.0
    depth: float = 1.0
    raised: bool = True

    def __post_init__(self) -> None:
        """Trim the text and clamp the geometry.

        The text is length-limited because it arrives from a prompt and a
        thousand-character string would take minutes to tessellate and produce
        something unprintable.
        """
        object.__setattr__(self, "text", str(self.text).strip()[:MAX_TEXT])
        object.__setattr__(self, "size", _clamp(self.size, 1.0, 200.0))
        object.__setattr__(self, "depth", _clamp(self.depth, 0.2, 20.0))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "text-on-surface"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {
            "text": self.text,
            "face": self.face.value,
            "size": self.size,
            "depth": self.depth,
            "raised": self.raised,
        }

    def describe(self) -> str:
        """A line for the feature tree."""
        how = "Emboss" if self.raised else "Engrave"
        return f'{how} "{self.text}" on the {self.face.value} at {self.size:g} mm'


@dataclass(frozen=True, slots=True)
class Extrude(Command):
    """Draw a closed outline and give it thickness.

    The operation CAD exists for, and the one the vocabulary was missing. A box,
    a cylinder and a sphere between them describe very little; a profile
    describes almost anything with a constant cross-section - a bracket, a
    gasket, a nameplate, the side of a case.

    The outline is a list of points in millimetres, closed automatically. Not a
    parametric sketch with constraints: those need a solver, a user interface
    to drive it, and a way to name edges that survives a rebuild. This is the
    useful nine tenths of that, and it is honest about being it.
    """

    points: tuple[tuple[float, float], ...]
    height: float
    plane: Plane = Plane.XY
    cut: bool = False

    def __post_init__(self) -> None:
        """Clamp the height and tidy the outline.

        Points arrive from a language model as readily as from a mouse, so the
        same rules apply: bounded, finite, and few enough to build.
        """
        object.__setattr__(self, "height", _clamp(self.height))
        object.__setattr__(self, "points", _tidy_outline(self.points))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "extrude"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {
            "points": [list(point) for point in self.points],
            "height": self.height,
            "plane": self.plane.value,
            "cut": self.cut,
        }

    def describe(self) -> str:
        """A line for the feature tree."""
        verb = "Cut" if self.cut else "Extrude"
        return (
            f"{verb} a {len(self.points)}-point outline {self.height:g} mm "
            f"on the {self.plane.value} plane"
        )

    @property
    def is_closed_enough(self) -> bool:
        """Whether there are enough points to enclose an area at all."""
        return len(self.points) >= MIN_OUTLINE_POINTS


@dataclass(frozen=True, slots=True)
class PushPull(Command):
    """Take hold of one face and move it, thickening or thinning the part.

    SketchUp's push/pull, and the operation people mean when they say they want
    to model rather than to configure. Everything else in this vocabulary makes
    a shape from numbers; this one changes a shape that already exists by
    pointing at part of it.

    **The face is named by a point on it**, which is the pragmatic answer to a
    genuinely hard problem. Faces have no stable identity across a rebuild -
    the tree is rebuilt from nothing every time, so there is no index, no name
    and no handle that survives an earlier feature changing. A point does
    survive, because it is in the same millimetres as everything else, and on
    the next rebuild the face nearest it is the face that was meant. That is
    not perfect and is not pretending to be: move something underneath it far
    enough and the wrong face is picked. It is the trade every kernel of this
    size makes, and the alternative is a constraint solver.

    ``distance`` is signed along the face's own outward normal: positive pulls
    material out, negative pushes it in. Which way that is on screen is
    therefore decided by the face, not by the axis, which is what makes it
    read the same whichever way the part has been turned.
    """

    at: tuple[float, float, float]
    """A point on the face to take hold of, in millimetres."""

    distance: float
    """How far to move it, along its own normal. Negative pushes in."""

    def __post_init__(self) -> None:
        """Keep the point and the distance inside what a kernel can survive."""
        object.__setattr__(
            self,
            "at",
            tuple(_clamp(value, -MAX_MM, MAX_MM) for value in tuple(self.at)[:3]),
        )
        object.__setattr__(self, "distance", _clamp(self.distance, -MAX_MM, MAX_MM))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "push-pull"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"at": list(self.at), "distance": self.distance}

    def describe(self) -> str:
        """A line for the feature tree."""
        verb = "Pull" if self.distance >= 0 else "Push"
        x, y, z = self.at
        return f"{verb} the face at ({x:g}, {y:g}, {z:g}) by {abs(self.distance):g} mm"

    @property
    def does_anything(self) -> bool:
        """Whether this is a move at all rather than a twitch."""
        return abs(self.distance) >= LEAST_PUSH_MM


@dataclass(frozen=True, slots=True)
class Mirror(Command):
    """Reflect the part and keep both halves.

    Half the mechanical parts anyone prints are symmetrical, and modelling one
    half and reflecting it is both faster and self-correcting: the two sides
    cannot drift apart, because there is only one of them.

    The mirror plane passes through the origin, which is where every shape in
    this vocabulary is centred, so the two halves meet rather than overlapping
    or leaving a gap.
    """

    plane: Plane = Plane.YZ
    keep_original: bool = True

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "mirror"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"plane": self.plane.value, "keep_original": self.keep_original}

    def describe(self) -> str:
        """A line for the feature tree."""
        both = "and keep both halves" if self.keep_original else "and replace it"
        return f"Mirror across the {self.plane.value} plane {both}"


@dataclass(frozen=True, slots=True)
class Repeat(Command):
    """Make a row of the last thing added, evenly spaced.

    A pattern in a real CAD package repeats a *feature*, not the whole model,
    and that is what this does: it takes the shape immediately before it in the
    tree and lays out copies. A row of mounting holes is one drilled hole and
    one of these, which is how people describe it out loud.

    Repeating anything that is not a shape - a fillet, a hollow - means
    nothing, and the compiler refuses it rather than producing a model that is
    subtly not what was asked for.
    """

    times: int = 2
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

    def __post_init__(self) -> None:
        """Bound the count and clamp the spacing.

        The count is capped because a runaway value is a boolean operation per
        copy, and a thousand of them is a rebuild that never returns.
        """
        object.__setattr__(self, "times", _clamp_count(self.times))
        for axis in ("dx", "dy", "dz"):
            object.__setattr__(self, axis, _clamp(getattr(self, axis), -MAX_MM, MAX_MM))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "repeat"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"times": self.times, "dx": self.dx, "dy": self.dy, "dz": self.dz}

    def describe(self) -> str:
        """A line for the feature tree."""
        step = f"({self.dx:g}, {self.dy:g}, {self.dz:g})"
        return f"Repeat the last shape {self.times} times, {step} mm apart"

    @property
    def goes_anywhere(self) -> bool:
        """Whether the copies land somewhere other than on top of each other."""
        return any(abs(step) >= MIN_MM for step in (self.dx, self.dy, self.dz))


@dataclass(frozen=True, slots=True)
class RepeatAround(Command):
    """Space copies of the last thing added evenly round an axis.

    A bolt circle: drill one hole off centre, then ask for six of them. The
    copies are spaced over a full turn, because a partial arc needs a start
    angle and a sweep, and nobody has wanted one yet.
    """

    times: int = 4
    axis: str = "Z"

    def __post_init__(self) -> None:
        """Bound the count and normalise the axis."""
        object.__setattr__(self, "times", _clamp_count(self.times))
        letter = str(self.axis).upper()[:1]
        object.__setattr__(self, "axis", letter if letter in "XYZ" else "Z")

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "repeat-around"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {"times": self.times, "axis": self.axis}

    def describe(self) -> str:
        """A line for the feature tree."""
        return f"Space {self.times} copies of the last shape evenly around {self.axis}"

    @property
    def step_degrees(self) -> float:
        """How far apart the copies sit, in degrees."""
        return 360.0 / self.times


@dataclass(frozen=True, slots=True)
class Revolve(Command):
    """Spin a profile round the upright axis to make a solid of revolution.

    The other half of what a profile is for. Anything round in plan - a vase, a
    knob, a wheel, a bottle, a lampshade, a funnel - is one outline and this.

    Each corner is a *radius* and a *height*, not an x and a y: the first
    number is how far that corner sits from the axis, the second how high. A
    profile that strays inside the axis is pushed back out rather than refused,
    because a corner at -2 mm is somebody drawing in the wrong corner rather
    than asking for something impossible.

    Only the upright axis. A shape lying down is this followed by a ``rotate``,
    and offering three axes where two of them are rarely what anyone meant is
    worse than offering the one that is.
    """

    points: tuple[tuple[float, float], ...]
    degrees: float = 360.0
    cut: bool = False

    def __post_init__(self) -> None:
        """Tidy the profile and keep it out of the axis."""
        object.__setattr__(self, "degrees", _clamp_arc(self.degrees))
        object.__setattr__(self, "points", _against_the_axis(_tidy_outline(self.points)))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "revolve"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {
            "points": [list(point) for point in self.points],
            "degrees": self.degrees,
            "cut": self.cut,
        }

    def describe(self) -> str:
        """A line for the feature tree."""
        verb = "Cut by spinning" if self.cut else "Spin"
        arc = "all the way round" if self.degrees >= 360.0 else f"{self.degrees:g} degrees"
        return f"{verb} a {len(self.points)}-point profile {arc}"

    @property
    def is_closed_enough(self) -> bool:
        """Whether there are enough corners to enclose an area at all."""
        return len(self.points) >= MIN_OUTLINE_POINTS

    @property
    def widest(self) -> float:
        """The finished radius, in millimetres."""
        return max((radius for radius, _ in self.points), default=0.0)


# ------------------------------------------------------- along and between

# A path is drawn or described, like an outline. The same cap applies for the
# same reason: beyond this it is a runaway rather than a request.
MAX_PATH_POINTS = 200

# Two points is the fewest that describe a direction to travel in.
MIN_PATH_POINTS = 2

# A swept corner with no bend radius at all is not a sharp corner - it is
# **wrong geometry**, silently. Measured: a 10 x 10 section along a right-angled
# 70 mm path came back at 3200 mm3 instead of 7000, with no error, because the
# outside of the miter folds through itself. Any bend at all fixes it exactly,
# so there is a floor rather than a choice.
MIN_BEND_MM = 0.1

# Two sections closer together than this are the same height as far as OCCT is
# concerned, and lofting between them fails with `StdFail_NotDone`.
MIN_SECTION_GAP_MM = 0.01


def _tidy_path(points: Any) -> tuple[tuple[float, float, float], ...]:
    """Clean a three-dimensional path into something a kernel can travel along.

    The same rules as an outline, with one difference: a path is **not**
    closed, so a last point that repeats the first is a real instruction to
    come back, not a redundant closing point.
    """
    cleaned: list[tuple[float, float, float]] = []
    for point in list(points)[:MAX_PATH_POINTS]:
        try:
            x, y, z = float(point[0]), float(point[1]), float(point[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        spot = (
            _clamp(x, -MAX_MM, MAX_MM),
            _clamp(y, -MAX_MM, MAX_MM),
            _clamp(z, -MAX_MM, MAX_MM),
        )
        if cleaned and _is_same_place(cleaned[-1], spot):
            continue
        cleaned.append(spot)
    return tuple(cleaned)


def _is_same_place(one: tuple[float, float, float], two: tuple[float, float, float]) -> bool:
    """Whether two path points are close enough to make a zero-length segment."""
    return all(abs(a - b) < MIN_MM for a, b in zip(one, two, strict=True))


def _segment_lengths(path: tuple[tuple[float, float, float], ...]) -> list[float]:
    """How long each straight run of a path is."""
    return [math.dist(start, end) for start, end in pairwise(path)]


def _corner_reach(path: tuple[tuple[float, float, float], ...]) -> list[float]:
    """How much straight run each corner eats per millimetre of bend radius.

    Rounding a corner of interior angle *theta* replaces it with an arc that
    starts ``r / tan(theta / 2)`` back along each leg. A right angle therefore
    eats exactly the radius; a hairpin eats several times it. Returning the
    multiplier rather than the length keeps this usable as a bound on *r*.

    Zero for a corner that is not one: three points in a line have nothing to
    round, and dividing by ``tan(90 degrees)`` would say so as infinity.
    """
    reaches: list[float] = []
    for before, corner, after in zip(path, path[1:], path[2:], strict=False):
        first = _unit(before, corner)
        second = _unit(after, corner)
        if first is None or second is None:
            reaches.append(0.0)
            continue
        cosine = max(-1.0, min(1.0, sum(a * b for a, b in zip(first, second, strict=True))))
        half = math.acos(cosine) / 2
        # Straight through: no corner, nothing eaten. Doubled back: everything.
        if half >= math.pi / 2 - 1e-9:
            reaches.append(0.0)
        elif half <= 1e-9:
            reaches.append(float("inf"))
        else:
            reaches.append(1.0 / math.tan(half))
    return reaches


def _unit(point: tuple[float, float, float], origin: tuple[float, float, float]) -> Any:
    """The direction from ``origin`` to ``point``, or ``None`` if there is none."""
    away = tuple(a - b for a, b in zip(point, origin, strict=True))
    length = math.sqrt(sum(component * component for component in away))
    if length < 1e-9:
        return None
    return tuple(component / length for component in away)


def widest_bend(path: tuple[tuple[float, float, float], ...]) -> float:
    """The largest bend radius this path's corners can actually take.

    Each straight run has to be long enough for the bends at both of its ends.
    Measured against OCCT: a path of 30, 20, 30, 20 mm runs accepts a 10 mm
    bend and refuses 11, which is exactly what this returns.

    Infinite when there is nothing to round, so a straight path never has its
    radius clamped to something meaningless.
    """
    runs = _segment_lengths(path)
    reaches = _corner_reach(path)
    if not runs or not any(reaches):
        return math.inf

    # Run i lies between corner i-1 and corner i, counting corners from the
    # second point of the path. The runs at either end serve one corner only.
    limits: list[float] = []
    for index, run in enumerate(runs):
        claimed = 0.0
        if index - 1 >= 0:
            claimed += reaches[index - 1]
        if index < len(reaches):
            claimed += reaches[index]
        if claimed > 0:
            limits.append(run / claimed)
    return min(limits) if limits else math.inf


@dataclass(frozen=True, slots=True)
class Sweep(Command):
    """Push an outline along a path to make a rail, a handle, a pipe or a trim.

    The third thing a profile is for, after growing it upwards and spinning it
    round. Anything with a constant cross-section that does *not* run in a
    straight line is this: a grab handle, a cable channel, a skirting trim, the
    tube in a bottle carrier.

    The cross-section is placed **square to the start of the path** rather than
    on a plane the user names. Getting that wrong produces no error: a section
    lying in the plane the path travels in sweeps to a volume of exactly zero,
    which looks like the command did nothing at all.

    ``bend_radius`` is not decoration. A mitred corner in a sweep folds through
    itself and OCCT reports no problem, so there is a floor on it - see
    ``MIN_BEND_MM``. It is also clamped to what the path's straight runs can
    give up, because a bend that does not fit fails in the kernel instead.
    """

    points: tuple[tuple[float, float], ...]
    path: tuple[tuple[float, float, float], ...]
    bend_radius: float = 2.0
    cut: bool = False

    def __post_init__(self) -> None:
        """Tidy both drawings, then fit the bend to the path it has to round."""
        object.__setattr__(self, "points", _tidy_outline(self.points))
        object.__setattr__(self, "path", _tidy_path(self.path))

        room = widest_bend(self.path)
        wanted = _clamp(self.bend_radius, MIN_BEND_MM, MAX_RADIUS_MM)
        object.__setattr__(self, "bend_radius", min(wanted, room))

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "sweep"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {
            "points": [list(point) for point in self.points],
            "path": [list(point) for point in self.path],
            "bend_radius": self.bend_radius,
            "cut": self.cut,
        }

    def describe(self) -> str:
        """A line for the feature tree."""
        verb = "Cut by sweeping" if self.cut else "Sweep"
        return f"{verb} a {len(self.points)}-point outline along a {self.length:g} mm path"

    @property
    def problem(self) -> str | None:
        """Why this cannot be built, in words the user can act on."""
        if len(self.points) < MIN_OUTLINE_POINTS:
            return "An outline needs at least three corners to have an inside."
        if len(self.path) < MIN_PATH_POINTS:
            return "A path needs at least two points to have a direction."
        if self.bend_radius < MIN_BEND_MM:
            return (
                "The path turns too sharply for the straight runs between its "
                "corners. Move the corners further apart."
            )
        return None

    @property
    def length(self) -> float:
        """How far the outline travels, along the corners as drawn."""
        return sum(_segment_lengths(self.path))

    @property
    def turns(self) -> bool:
        """Whether the path bends at all, rather than running straight."""
        return any(_corner_reach(self.path))


@dataclass(frozen=True, slots=True)
class Section:
    """One outline at one height, as a step in a loft.

    A value rather than a pair of loose lists, because the two travel together
    everywhere and a loft with its heights and its outlines out of step is a
    shape nobody asked for.
    """

    points: tuple[tuple[float, float], ...]
    height: float

    def __post_init__(self) -> None:
        """Tidy the outline and keep the height inside the world."""
        object.__setattr__(self, "points", _tidy_outline(self.points))
        object.__setattr__(self, "height", _clamp(self.height, -MAX_MM, MAX_MM))

    @property
    def is_closed_enough(self) -> bool:
        """Whether this outline encloses an area at all."""
        return len(self.points) >= MIN_OUTLINE_POINTS


# Every section is a surface OCCT has to blend through. This is far beyond what
# anyone draws and low enough that a runaway list cannot stall a rebuild.
MAX_SECTIONS = 40


@dataclass(frozen=True, slots=True)
class Loft(Command):
    """Blend between outlines stacked at different heights.

    What extrude cannot do: a cross-section that *changes* on the way up. A
    tapered plant pot, a funnel, a boat hull, a wedge that starts rectangular
    and finishes round, the transition between a round duct and a square one.

    The sections are sorted by height rather than taken in the order given,
    because a list that arrives out of order describes the same solid and
    refusing it would be pedantry. Two at the same height are refused: OCCT
    fails on them with ``StdFail_NotDone``, and there is no sensible reading of
    what shape was meant.
    """

    sections: tuple[Section, ...]
    cut: bool = False

    def __post_init__(self) -> None:
        """Sort the sections by height and cap how many there may be."""
        ordered = tuple(sorted(self.sections[:MAX_SECTIONS], key=lambda s: s.height))
        object.__setattr__(self, "sections", ordered)

    @property
    def name(self) -> str:
        """The feature name recorded in the document."""
        return "loft"

    @property
    def parameters(self) -> dict[str, Any]:
        """Everything needed to rebuild this feature."""
        return {
            "sections": [
                {"points": [list(point) for point in s.points], "height": s.height}
                for s in self.sections
            ],
            "cut": self.cut,
        }

    def describe(self) -> str:
        """A line for the feature tree."""
        verb = "Cut by blending" if self.cut else "Blend"
        return f"{verb} between {len(self.sections)} outlines over {self.rise:g} mm"

    @property
    def problem(self) -> str | None:
        """Why this cannot be built, in words the user can act on."""
        if len(self.sections) < 2:
            return "A blend needs at least two outlines to blend between."
        if any(not section.is_closed_enough for section in self.sections):
            return "Every outline needs at least three corners to have an inside."
        for lower, upper in pairwise(self.sections):
            if upper.height - lower.height < MIN_SECTION_GAP_MM:
                return (
                    f"Two outlines are both at {lower.height:g} mm. Give each one its own height."
                )
        return None

    @property
    def rise(self) -> float:
        """How tall the blend is, bottom section to top."""
        if len(self.sections) < 2:
            return 0.0
        return self.sections[-1].height - self.sections[0].height


# --------------------------------------------------------------- rebuilding

_BY_NAME: dict[str, Any] = {
    "place-mesh": PlaceMesh,
    "create-box": CreateBox,
    "create-cylinder": CreateCylinder,
    "create-sphere": CreateSphere,
    "fillet": Fillet,
    "chamfer": Chamfer,
    "hollow": Hollow,
    "move": Move,
    "rotate": Rotate,
    "scale-to": ScaleTo,
    "extrude": Extrude,
    "mirror": Mirror,
    "repeat": Repeat,
    "repeat-around": RepeatAround,
    "revolve": Revolve,
    "sweep": Sweep,
    "loft": Loft,
    "text-on-surface": TextOnSurface,
    "push-pull": PushPull,
}


def command_from(feature: Feature) -> Command | None:
    """Rebuild a command from a recorded feature.

    Returns ``None`` for a name this build does not know, rather than raising.
    A document saved by a newer version must open in an older one with the
    unknown feature skipped and *said out loud*, not refuse to open at all.
    """
    factory = _BY_NAME.get(feature.name)
    if factory is None:
        return None
    try:
        return _construct(factory, feature.parameters)
    except (TypeError, ValueError, KeyError):
        return None


def _construct(factory: Any, parameters: dict[str, Any]) -> Command:
    """Build one command from its recorded parameters."""
    if factory is Fillet:
        return Fillet(parameters["radius"], EdgeSelector(parameters.get("edges", "all")))
    if factory is Chamfer:
        return Chamfer(parameters["distance"], EdgeSelector(parameters.get("edges", "all")))
    if factory is Hollow:
        opening = parameters.get("opening")
        return Hollow(parameters["wall_thickness"], Face(opening) if opening else None)
    if factory is PushPull:
        at = tuple(float(value) for value in parameters["at"])
        return PushPull(at, float(parameters["distance"]))  # type: ignore[arg-type]
    if factory is PlaceMesh:
        return PlaceMesh(str(parameters["source"]), str(parameters.get("note", "")))
    if factory is ScaleTo:
        return ScaleTo(Length.mm(parameters["height_mm"]))
    if factory is Extrude:
        return Extrude(
            tuple(tuple(point) for point in parameters["points"]),
            parameters["height"],
            Plane(parameters.get("plane", "floor")),
            bool(parameters.get("cut", False)),
        )
    if factory is Revolve:
        return Revolve(
            tuple(tuple(point) for point in parameters["points"]),
            parameters.get("degrees", 360.0),
            bool(parameters.get("cut", False)),
        )
    if factory is Sweep:
        return Sweep(
            tuple(tuple(point) for point in parameters["points"]),
            tuple(tuple(point) for point in parameters["path"]),
            parameters.get("bend_radius", 2.0),
            bool(parameters.get("cut", False)),
        )
    if factory is Loft:
        return Loft(
            tuple(
                Section(tuple(tuple(point) for point in s["points"]), s["height"])
                for s in parameters["sections"]
            ),
            bool(parameters.get("cut", False)),
        )
    if factory is Mirror:
        return Mirror(
            Plane(parameters.get("plane", "side")),
            bool(parameters.get("keep_original", True)),
        )
    if factory is TextOnSurface:
        return TextOnSurface(
            parameters["text"],
            Face(parameters.get("face", "front")),
            parameters.get("size", 10.0),
            parameters.get("depth", 1.0),
            bool(parameters.get("raised", True)),
        )
    result: Command = factory(**parameters)
    return result


def known_commands() -> tuple[str, ...]:
    """Every command name this build understands.

    The vocabulary handed to a language model, and the list a document loader
    checks against.
    """
    return tuple(sorted(_BY_NAME))
