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

from dataclasses import dataclass
from enum import Enum
from typing import Any

from modelpop.domain.commands import Command, Feature
from modelpop.domain.units import Length

__all__ = [
    "MAX_COPIES",
    "MAX_OUTLINE_POINTS",
    "MIN_OUTLINE_POINTS",
    "Chamfer",
    "CreateBox",
    "CreateCylinder",
    "CreateSphere",
    "EdgeSelector",
    "Extrude",
    "Face",
    "Fillet",
    "Hollow",
    "Mirror",
    "Move",
    "Plane",
    "Repeat",
    "RepeatAround",
    "Revolve",
    "Rotate",
    "ScaleTo",
    "TextOnSurface",
    "command_from",
    "known_commands",
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


def _where(command: Any) -> str:
    """A phrase naming where a shape sits, or nothing when it is centred."""
    if command.x == 0 and command.y == 0 and command.z == 0:
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
        return f"{_verb(self)} a {shape}{_where(self)}"


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
        return f"Add a {shape}{_where(self)}"


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
        return f"{_verb(self)} a {self.radius:g} mm radius sphere{_where(self)}"


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
class ScaleTo(Command):
    """Resize the part so its tallest dimension is a stated size.

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


# --------------------------------------------------------------- rebuilding

_BY_NAME: dict[str, Any] = {
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
    "text-on-surface": TextOnSurface,
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
