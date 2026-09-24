"""Where a part sits, worked out as a move rather than applied to geometry.

Every one of these returns a ``Move``, which is the point. A part with a
feature tree is not repositioned by rewriting its vertices - it is repositioned
by adding a step that says so, which rebuilds, undoes and shows up in the tree
like everything else (ADR-0001). The alternative, nudging the mesh directly,
puts the model and its own description out of step the moment anything earlier
in the tree changes.

The two that matter are settling a part onto the bed and centring it over the
plate. Both were things the user had to do by eye with a drag handle, which is
a poor way to hit zero exactly.
"""

from __future__ import annotations

from modelpop.domain.cad_commands import Move
from modelpop.domain.mesh import BoundingBox

__all__ = ["centre_over_bed", "nudge", "settle_onto_bed"]


def settle_onto_bed(box: BoundingBox) -> Move:
    """Drop the part until its lowest point rests on z = 0.

    Lifts as well as drops: a part floating above the plate and a part sunk
    halfway through it are the same mistake, and both are fixed by putting the
    bottom of the bounding box on zero.
    """
    return Move(dz=-box.min_z)


def centre_over_bed(box: BoundingBox) -> Move:
    """Slide the part so it straddles the middle of the plate.

    Height is left alone. Centring and settling are separate on purpose, so
    neither undoes the other and each reads as one step in the tree.
    """
    centre_x, centre_y, _ = box.centre
    return Move(dx=-centre_x, dy=-centre_y)


def nudge(axis: str, distance_mm: float) -> Move:
    """One step along a named axis.

    Args:
        axis: ``X``, ``Y`` or ``Z``; anything else is taken as ``Z``, matching
            what ``Rotate`` does with an axis it does not recognise.
        distance_mm: how far, signed.
    """
    letter = str(axis).upper()[:1]
    if letter == "X":
        return Move(dx=distance_mm)
    if letter == "Y":
        return Move(dy=distance_mm)
    return Move(dz=distance_mm)
