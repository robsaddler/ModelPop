"""Cutting the view open to see inside a part.

A hollow is the one operation whose result you cannot check by looking. The
outside of a hollowed box is identical to the outside of a solid one, so
"did that wall come out at 2 mm" is answerable only by slicing it, exporting
it, or trusting the number that was typed. This makes it answerable by looking.

It cuts the **view**, not the model. Nothing here reaches the command bus, the
feature tree or anything that gets exported - which is why it can be dragged
about freely and why turning it off restores exactly what was there. A section
that quietly modified the part would be a booby trap.

No UI framework and no VTK, so where the cut goes and what the user is told
about it are driven by a test with no display. The viewport turns a plane into
clipping and knows nothing about why; this knows why and nothing about VTK.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modelpop.domain.mesh import BoundingBox

__all__ = ["Axis", "SectionPlane", "SectionTool"]


class Axis(Enum):
    """Which way the cut faces."""

    X = "across"
    Y = "back"
    Z = "up"

    @property
    def normal(self) -> tuple[float, float, float]:
        """The direction material is removed in."""
        return {Axis.X: (1.0, 0.0, 0.0), Axis.Y: (0.0, 1.0, 0.0), Axis.Z: (0.0, 0.0, 1.0)}[self]

    @property
    def describe(self) -> str:
        """A phrase for the interface."""
        return {
            Axis.X: "left to right",
            Axis.Y: "front to back",
            Axis.Z: "top to bottom",
        }[self]


@dataclass(frozen=True, slots=True)
class SectionPlane:
    """Where to cut, and which side to keep.

    The offset is in millimetres along the axis, in the same world coordinates
    as everything else on the plate, so a plane at zero cuts through the origin
    - which is where every shape in the CAD vocabulary is centred, and
    therefore the cut people want by default.
    """

    axis: Axis = Axis.X
    offset: float = 0.0
    flipped: bool = False
    """Keep the other half. The same cut seen from the other side."""

    @property
    def origin(self) -> tuple[float, float, float]:
        """A point the plane passes through, in millimetres."""
        x, y, z = self.axis.normal
        return (x * self.offset, y * self.offset, z * self.offset)

    @property
    def normal(self) -> tuple[float, float, float]:
        """Which side of the plane is kept.

        VTK keeps what the normal points *away* from, so this is the direction
        of the half that is thrown away.
        """
        sign = -1.0 if self.flipped else 1.0
        return tuple(component * sign for component in self.axis.normal)  # type: ignore[return-value]


# A cut exactly on the face of a part shows nothing, so the slider never quite
# reaches either end of the model. In millimetres.
EDGE_MARGIN_MM = 0.5


class SectionTool:
    """Where the section plane is, and what the interface may offer.

    The range it can travel is taken from the model on screen rather than from
    the build volume. A 20 mm part inside a 256 mm envelope would otherwise
    give a slider where every useful position is within a hair's breadth of the
    middle, and the cut would jump straight past the part.
    """

    def __init__(self) -> None:
        """Start switched off, cutting across the middle."""
        self._on = False
        self._plane = SectionPlane()
        self._bounds: BoundingBox | None = None

    # -------------------------------------------------------------- switching

    @property
    def is_on(self) -> bool:
        """Whether the view is currently cut open."""
        return self._on

    def turn_on(self) -> None:
        """Cut the view open, across the middle of whatever is on screen."""
        self._on = True
        self._plane = SectionPlane(self._plane.axis, self.middle, self._plane.flipped)

    def turn_off(self) -> None:
        """Show the whole model again."""
        self._on = False

    def toggle(self) -> bool:
        """Switch the section on or off. Returns whether it is now on."""
        self.turn_off() if self._on else self.turn_on()
        return self._on

    # ---------------------------------------------------------------- aiming

    @property
    def plane(self) -> SectionPlane | None:
        """The plane to cut with, or ``None`` when the section is off."""
        return self._plane if self._on else None

    @property
    def axis(self) -> Axis:
        """Which way the cut currently faces."""
        return self._plane.axis

    @property
    def offset(self) -> float:
        """How far along that axis the cut sits, in millimetres."""
        return self._plane.offset

    @property
    def flipped(self) -> bool:
        """Whether the other half is being kept."""
        return self._plane.flipped

    def cut_along(self, axis: Axis) -> None:
        """Turn the cut to face a different way.

        The offset moves to the middle of the new axis rather than carrying
        over. Carrying it over puts the plane outside a part that is long in
        one direction and short in another, so the model vanishes and the tool
        looks broken.
        """
        self._plane = SectionPlane(axis, 0.0, self._plane.flipped)
        self._plane = SectionPlane(axis, self.middle, self._plane.flipped)

    def move_to(self, offset: float) -> None:
        """Slide the cut along its axis, in millimetres.

        Clamped to the model, less a margin at each end: a cut exactly on a
        face shows either the whole part or none of it, and both read as the
        slider having stopped working.
        """
        low, high = self.travel
        self._plane = SectionPlane(
            self._plane.axis, max(low, min(high, float(offset))), self._plane.flipped
        )

    def flip(self) -> None:
        """Keep the other half of the model instead."""
        self._plane = SectionPlane(self._plane.axis, self._plane.offset, not self._plane.flipped)

    # ---------------------------------------------------------- what there is

    def fits(self, bounds: BoundingBox | None) -> None:
        """Take the bounding box of whatever is now on screen.

        Called on every new model. The offset is pulled back inside the new
        bounds, so changing to a smaller part does not leave the cut stranded
        outside it with nothing on screen.
        """
        self._bounds = bounds
        self.move_to(self._plane.offset if bounds is not None else 0.0)

    @property
    def travel(self) -> tuple[float, float]:
        """How far the cut may slide, low to high, in millimetres."""
        box = self._bounds
        if box is None:
            return (0.0, 0.0)
        low, high = {
            Axis.X: (box.min_x, box.max_x),
            Axis.Y: (box.min_y, box.max_y),
            Axis.Z: (box.min_z, box.max_z),
        }[self._plane.axis]
        if high - low <= 2 * EDGE_MARGIN_MM:
            middle = (low + high) / 2
            return (middle, middle)
        return (low + EDGE_MARGIN_MM, high - EDGE_MARGIN_MM)

    @property
    def middle(self) -> float:
        """Halfway along the current axis, which is where a cut starts."""
        low, high = self.travel
        return (low + high) / 2

    @property
    def has_something_to_cut(self) -> bool:
        """Whether there is a model on screen to cut open."""
        return self._bounds is not None

    def describe(self) -> str:
        """What to show the user right now, whatever state it is in."""
        if not self._on:
            return "The view is not cut open."
        if not self.has_something_to_cut:
            return "There is nothing on the plate to cut open."
        half = "far" if self._plane.flipped else "near"
        return (
            f"Cut {self._plane.axis.describe} at {self._plane.offset:.1f} mm, "
            f"keeping the {half} half."
        )
