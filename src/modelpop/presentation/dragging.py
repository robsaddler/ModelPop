"""Turning a drag in the viewport into commands the feature tree understands.

Dragging a part about is the obvious thing to want to do to a 3D model, and up
to now every position in this application has been typed. The handles
themselves belong to VTK; what they *mean* belongs here.

The whole point is that a drag is not a special case. It ends as the same
``Move`` and ``Rotate`` commands the toolbar emits, so it lands in the feature
tree, reads back as a sentence, and undoes in one step - rather than being a
transform hanging off the actor that the model knows nothing about and that
disappears the next time the tree rebuilds.

That is also why this refuses rather than approximates. The vocabulary has
rotation about *one* axis; a drag that twisted about two is not something it
can say, and rounding it to the nearest axis would silently move the part
somewhere nobody asked for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from modelpop.domain.cad_commands import Move, Rotate

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from modelpop.domain.commands import Command

__all__ = ["Drag", "movement_in"]

# Below this a drag is a twitch, not an instruction. In millimetres and
# degrees: a handle nudged by a tenth of a millimetre should leave the tree
# alone rather than filling it with steps nobody can see the effect of.
LEAST_MOVE_MM = 0.05
LEAST_TURN_DEGREES = 0.5

# Below this a resize is a twitch rather than an instruction. Half a percent
# is well under what anybody can see.
LEAST_GROWTH_TO_RECORD = 0.005

# How far off an axis a rotation may be and still count as being about it.
# A drag on the widget's own rings is constrained to one axis, so anything
# further out than this came from somewhere unexpected.
AXIS_TOLERANCE = 0.02


@dataclass(frozen=True, slots=True)
class Drag:
    """What one drag of the handles amounted to.

    Both commands are optional and both may be present: the handles allow a
    translation and a rotation before the button comes up again.
    """

    move: Move | None = None
    turn: Rotate | None = None
    resize: float = 1.0
    """What the drag scaled the part by. 1.0 when it did not.

    A proportion rather than a command, because the command has to be an
    absolute size and only the caller knows how big the part is now. Keeping
    the conversion out here is also what stops this module needing to know
    what it is looking at.
    """

    refused: str = ""
    """Why part of the drag could not be expressed, if any of it could not."""

    @property
    def commands(self) -> tuple[Command, ...]:
        """The commands to apply, in the order they must be applied.

        The turn goes first. Both are about the origin, and a part that has
        been moved off the origin rotates about a point it no longer sits on -
        which is a different shape from the one the user dragged.
        """
        return tuple(c for c in (self.turn, self.move) if c is not None)

    @property
    def is_a_resize(self) -> bool:
        """Whether this drag changed the size.

        A resize arrives alone: the corner grips do nothing but scale, and the
        translation in their matrix is the scale's own compensation for
        growing about the part's centre rather than about the origin.
        """
        return abs(self.resize - 1.0) >= LEAST_GROWTH_TO_RECORD

    @property
    def did_anything(self) -> bool:
        """Whether this drag is worth recording at all."""
        return bool(self.commands) or self.is_a_resize

    def describe(self) -> str:
        """A line for the status bar."""
        if not self.did_anything:
            return self.refused or "That drag did not move anything."
        if self.is_a_resize:
            said = f"Resize to {self.resize * 100:.0f}% of its size"
        else:
            said = ", then ".join(c.describe() for c in self.commands)
        return f"{said}.{(' ' + self.refused) if self.refused else ''}"


def movement_in(matrix: NDArray[np.floating] | list[list[float]]) -> Drag:
    """Read a 4x4 transform as the commands that would reproduce it.

    The matrix comes from VTK, which is why it arrives here as numbers rather
    than as anything with an opinion: this module must stay drivable by a test
    with no graphics context at all.
    """
    grid = np.asarray(matrix, dtype=np.float64)
    if grid.shape != (4, 4) or not np.all(np.isfinite(grid)):
        return Drag(refused="That drag could not be read.")

    scale, uneven = _scale_in(grid)
    if uneven:
        return Drag(refused=uneven)

    if abs(scale - 1.0) >= LEAST_GROWTH_TO_RECORD:
        # A corner grip scales and does nothing else, and the translation it
        # leaves behind is the scale growing about the part's centre rather
        # than about the origin. Reading that column as a Move as well would
        # shift the part by however far its centre is from zero.
        return Drag(resize=scale)

    move = _translation_in(grid)
    turn, refused = _rotation_in(grid / scale if scale else grid)
    return Drag(move=move, turn=turn, refused=refused)


def _scale_in(grid: NDArray[np.floating]) -> tuple[float, str]:
    """How much the matrix grew the part, and why it could not be read.

    Uniform only. The handles cannot scale one axis, so an uneven one is a
    corrupt matrix rather than an instruction, and guessing at it would be
    worse than saying so.
    """
    lengths = np.linalg.norm(grid[0:3, 0:3], axis=1)
    if not np.all(np.isfinite(lengths)) or float(np.min(lengths)) <= 0.0:
        return 1.0, "That drag could not be read."
    if not np.allclose(lengths, lengths[0], rtol=1e-3):
        return 1.0, "That drag stretched it unevenly, which cannot be recorded."
    return float(lengths[0]), ""


def _translation_in(grid: NDArray[np.floating]) -> Move | None:
    """The shift, if it is big enough to mean anything."""
    dx, dy, dz = (float(value) for value in grid[0:3, 3])
    if max(abs(dx), abs(dy), abs(dz)) < LEAST_MOVE_MM:
        return None
    return Move(dx, dy, dz)


def _rotation_in(grid: NDArray[np.floating]) -> tuple[Rotate | None, str]:
    """The turn, if there is one and the vocabulary can say it."""
    spin = grid[0:3, 0:3]

    # Any uniform scale has already been divided out by the caller, so rows
    # that are still not unit length mean a matrix nothing here produced.
    lengths = np.linalg.norm(spin, axis=1)
    if not np.allclose(lengths, 1.0, atol=1e-3):
        return None, "That drag could not be read as a turn."

    # The angle comes from the trace; the axis from the antisymmetric part.
    cosine = float(np.clip((float(np.trace(spin)) - 1.0) / 2.0, -1.0, 1.0))
    degrees = math.degrees(math.acos(cosine))
    if degrees < LEAST_TURN_DEGREES:
        return None, ""

    direction = np.array(
        [
            spin[2, 1] - spin[1, 2],
            spin[0, 2] - spin[2, 0],
            spin[1, 0] - spin[0, 1],
        ]
    )
    size = float(np.linalg.norm(direction))
    if size < 1e-9:
        # A half turn: the antisymmetric part vanishes and the axis has to come
        # from the diagonal instead. Rare, and wrong in a way nobody would spot.
        return _half_turn_in(spin)

    direction = direction / size
    biggest = int(np.argmax(np.abs(direction)))
    others = [abs(direction[i]) for i in range(3) if i != biggest]
    if max(others) > AXIS_TOLERANCE:
        return None, (
            "That turn was about more than one axis, which cannot be recorded. "
            "Turn about one axis at a time."
        )

    axis = "XYZ"[biggest]
    if direction[biggest] < 0:
        degrees = -degrees
    return Rotate(degrees, axis), ""


def _half_turn_in(spin: NDArray[np.floating]) -> tuple[Rotate | None, str]:
    """A rotation of exactly 180 degrees, whose axis the usual route cannot see."""
    diagonal = np.diag(spin)
    biggest = int(np.argmax(diagonal))
    if not math.isclose(float(diagonal[biggest]), 1.0, abs_tol=1e-3):
        return None, "That turn was about more than one axis, which cannot be recorded."
    return Rotate(180.0, "XYZ"[biggest]), ""
