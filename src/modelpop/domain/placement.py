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

__all__ = [
    "PRINTABLE_AT_LEAST_MM",
    "SENSIBLE_SIZE_MM",
    "centre_over_bed",
    "needs_a_sensible_size",
    "nudge",
    "settle_onto_bed",
]

# Below this a model is not a small part, it is a model with no scale at all.
#
# Measured on real files rather than guessed. A mesh from a picture arrives
# about one millimetre across. Two models downloaded from Thingiverse came in
# at 7.9 mm and 7.2 mm - and the second carries 1,132,190 triangles, which
# nobody authors for something the size of a pea. Neither was a small part;
# both were authored in units nobody wrote down.
#
# The line is drawn generously because the cost is asymmetric. A genuine 12 mm
# part resized is one undo away and says in its own name that the size was
# chosen; a 7 mm dragon left alone is invisible on a 256 mm plate and prints
# as a speck.
PRINTABLE_AT_LEAST_MM = 15.0

# What to make one instead: big enough to see and to print, small enough to sit
# on any plate. The user is expected to set the real size; this is only so the
# thing is visible and workable when it arrives.
SENSIBLE_SIZE_MM = 60.0


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


def needs_a_sensible_size(box: BoundingBox, envelope_mm: float) -> float:
    """What to scale a model to on arrival, or zero to leave it alone.

    A mesh that came from a picture or a set of photographs carries no scale:
    the units are whatever the generator happened to use, and a whole model one
    millimetre across is the normal case rather than a strange one. Dropped
    onto a 256 mm plate it is invisible, and every measurement taken off it is
    meaningless.

    Only the two implausible cases are touched - far too small to print, or
    larger than the machine could ever hold. Anything in between is left
    exactly as it arrived, because a part that is deliberately 5 mm is a part
    nobody should have resized behind their back.
    """
    largest = box.largest_dimension.millimetres
    if largest <= 0:
        return 0.0
    if largest < PRINTABLE_AT_LEAST_MM or largest > envelope_mm:
        return SENSIBLE_SIZE_MM
    return 0.0
