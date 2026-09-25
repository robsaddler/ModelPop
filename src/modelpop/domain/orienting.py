"""Which way up a model should print.

Overhanging surface is what needs supports, and supports are what make a print
slow, wasteful and scarred where they are torn off. Turning the model before
slicing is the cheapest way to have less of it.

Two numbers decide it, and only using both gives a usable answer. Overhang is
the thing being minimised; **steadiness** is what stops the answer being
nonsense. Measured on the dragon: the orientation with the least overhang -
17.0% against 20.9% as it stood - balances it upside down on three hundredths
of a square millimetre. It would fall over before the first layer finished. The
best orientation it can actually stand up in is 19.9%, which is a point better
and honest.

Nothing here touches geometry. The search belongs to the mesh adapter; what
lives here is the shape of its answer and the arithmetic that turns a direction
into the turns that get there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from modelpop.domain.cad_commands import Rotate

__all__ = ["STEADY_ENOUGH", "WORTH_TURNING_FOR", "Resting", "turns_that_put_down"]

# How much overhang has to go away before turning the model is worth it, as a
# share of its surface. Below this the model is already about as good as it
# gets, and rotating it anyway would churn the feature tree, move it off the
# spot the user chose and change nothing they could measure.
WORTH_TURNING_FOR = 0.02

# Below this share of the widest face of the convex hull, a resting position is
# a balancing act rather than a base.
STEADY_ENOUGH = 0.25


def turns_that_put_down(direction: np.ndarray) -> tuple[Rotate, ...]:
    """The turns that would leave ``direction`` pointing at the bed.

    The vocabulary has rotation about one named axis at a time, so an arbitrary
    orientation comes back as up to three of them, applied in the order given:
    X, then Y, then Z. That keeps every step something the feature tree can
    record, undo and describe - a drag and a toolbar click end as the same kind
    of command, and so does this.

    Angles under a tenth of a degree are dropped. They are noise from the
    decomposition and a tree full of "rotate 0.03 degrees about X" is a tree
    nobody can read.
    """
    down = np.asarray(direction, dtype=np.float64)
    length = float(np.linalg.norm(down))
    if length < 1e-9:
        return ()
    down = down / length

    spin = _rotation_taking(down, np.array([0.0, 0.0, -1.0]))
    # R = Rz @ Ry @ Rx, which is what applying X then Y then Z comes to.
    pitch = math.asin(float(np.clip(-spin[2, 0], -1.0, 1.0)))
    if abs(math.cos(pitch)) < 1e-7:
        # Straight up or straight down: roll and yaw fold into each other, so
        # put all of it in one of them rather than splitting it arbitrarily.
        roll, yaw = math.atan2(-spin[1, 2], spin[1, 1]), 0.0
    else:
        roll = math.atan2(spin[2, 1], spin[2, 2])
        yaw = math.atan2(spin[1, 0], spin[0, 0])

    turns = [
        Rotate(math.degrees(angle), axis)
        for angle, axis in ((roll, "X"), (pitch, "Y"), (yaw, "Z"))
        if abs(math.degrees(angle)) >= 0.1
    ]
    return tuple(turns)


def _rotation_taking(this: np.ndarray, to_that: np.ndarray) -> np.ndarray:
    """The shortest rotation carrying one unit vector onto another."""
    axis = np.cross(this, to_that)
    sine = float(np.linalg.norm(axis))
    cosine = float(np.dot(this, to_that))

    if sine < 1e-9:
        if cosine > 0:
            return np.eye(3)
        # Exactly opposite: any perpendicular axis does, so pick one that is
        # definitely not parallel to the vector being turned.
        fallback = np.array([1.0, 0.0, 0.0])
        if abs(float(np.dot(this, fallback))) > 0.9:
            fallback = np.array([0.0, 1.0, 0.0])
        axis = np.cross(this, fallback)
        axis = axis / float(np.linalg.norm(axis))
        return _about(axis, math.pi)

    return _about(axis / sine, math.atan2(sine, cosine))


def _about(axis: np.ndarray, radians: float) -> np.ndarray:
    """Rodrigues' rotation, as a 3x3."""
    x, y, z = axis
    cross = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    spin: np.ndarray = (
        np.eye(3) + math.sin(radians) * cross + (1 - math.cos(radians)) * (cross @ cross)
    )
    return spin


@dataclass(frozen=True, slots=True)
class Resting:
    """One way the model could stand on the plate, and what it would cost."""

    down: tuple[float, float, float]
    """Which way the model's own up would point, once it is turned."""

    overhang_now: float
    """Share of the surface that overhangs as the model stands."""

    overhang_then: float
    """Share that would overhang once it is turned."""

    steadiness: float = 0.0
    """How broad a base it would rest on, against the broadest it has. 0 to 1."""

    turns: tuple[Rotate, ...] = field(default_factory=tuple)
    """The commands that would get it there, in the order to apply them."""

    @property
    def is_steady(self) -> bool:
        """Whether it would stand up rather than balance."""
        return self.steadiness >= STEADY_ENOUGH

    @property
    def saves(self) -> float:
        """How much overhang turning it would remove."""
        return self.overhang_now - self.overhang_then

    @property
    def is_worth_it(self) -> bool:
        """Whether turning it would repay churning the feature tree."""
        return bool(self.turns) and self.saves >= WORTH_TURNING_FOR

    def describe(self) -> str:
        """What this would do, for the status bar."""
        if not self.turns:
            return (
                f"It is already lying the best way up - {self.overhang_now:.0%} of it overhangs "
                "and nothing else it can stand on does better."
            )
        if not self.is_worth_it:
            return (
                f"It is already close to the best way up: turning it would take the overhang "
                f"from {self.overhang_now:.0%} to {self.overhang_then:.0%}, which is not worth "
                "moving it for."
            )
        return (
            f"Overhang {self.overhang_now:.0%} to {self.overhang_then:.0%}, "
            f"turning it {' then '.join(t.describe().lower() for t in self.turns)}."
        )
