"""Turning the view, with any axis held still.

Dragging a trackball view gives an arbitrary angle: you meant to spin the part
left a little and it arrives tilted, with no way back to square except by eye.

A lock **forbids** turning about the axis it names. Lock X and Y together and
only Z is left, which is a turntable - the view spins round the plate and the
horizon never rolls, however far the drag goes. Lock nothing and it behaves as
it always did. Panning and zooming are untouched throughout.

The arithmetic is deliberately plain. A drag asks for two turns: sideways about
the world's upright axis, and up-and-down about whichever way the camera calls
right. Each is projected off the locked axes before it is applied, so what is
locked simply cannot appear in the result - rather than being applied and then
corrected, which is how a view ends up creeping.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable

    from numpy.typing import NDArray

__all__ = ["AXES", "LockedTurning", "allowed_axis", "turned_about"]

# How far the view turns per pixel dragged. Fast enough to be useful, slow
# enough to stop where you meant.
DEGREES_PER_PIXEL = 0.4

AXES: dict[str, NDArray[np.float64]] = {
    "X": np.array([1.0, 0.0, 0.0]),
    "Y": np.array([0.0, 1.0, 0.0]),
    "Z": np.array([0.0, 0.0, 1.0]),
}

# Below this a drag is a twitch rather than a turn.
LEAST_PIXELS = 1

# Below this, what is left of a rotation axis after the locked directions are
# taken out of it is numerical dust rather than an instruction.
LEAST_AXIS = 1e-6


def allowed_axis(wanted: NDArray[np.float64], locked: Iterable[str]) -> NDArray[np.float64] | None:
    """What is left of a rotation axis once the locked directions are removed.

    ``None`` when nothing is left - a sideways drag with Z locked asks for
    exactly the turn that is forbidden, and the honest answer is that the view
    does not move.
    """
    remaining = np.array(wanted, dtype=np.float64)
    for name in locked:
        axis = AXES.get(name.upper()[:1])
        if axis is not None:
            remaining = remaining - float(np.dot(remaining, axis)) * axis

    length = float(np.linalg.norm(remaining))
    if length < LEAST_AXIS:
        return None
    return remaining / length


def turned_about(
    axis: NDArray[np.float64], degrees: float, point: NDArray[np.float64]
) -> NDArray[np.float64]:
    """A rotation about a world axis through a point, as a 4x4.

    A 4x4 so one object moves both a position, which needs the translation,
    and a direction, which does not.
    """
    angle = np.radians(degrees)
    cosine, sine = float(np.cos(angle)), float(np.sin(angle))
    x, y, z = axis / np.linalg.norm(axis)
    spin = np.array(
        [
            [
                cosine + x * x * (1 - cosine),
                x * y * (1 - cosine) - z * sine,
                x * z * (1 - cosine) + y * sine,
            ],
            [
                y * x * (1 - cosine) + z * sine,
                cosine + y * y * (1 - cosine),
                y * z * (1 - cosine) - x * sine,
            ],
            [
                z * x * (1 - cosine) - y * sine,
                z * y * (1 - cosine) + x * sine,
                cosine + z * z * (1 - cosine),
            ],
        ]
    )
    matrix = np.eye(4)
    matrix[:3, :3] = spin
    matrix[:3, 3] = point - spin @ point
    return matrix


class LockedTurning:
    """Turns a view by dragging, with any world axis held still.

    Listens ahead of the camera controls and takes the drag away from them
    while anything is locked, so the trackball never gets to tumble. With
    nothing locked it does not interfere at all. Panning, the wheel and the
    drag handles are never claimed.
    """

    def __init__(self, plotter: Any) -> None:
        """Watch a plotter's interactor. Idle until something is locked."""
        self._plotter = plotter
        self._locked: set[str] = set()
        self._turning = False
        self._from: tuple[int, int] | None = None
        self._observers: list[int] = []

    @property
    def locked(self) -> frozenset[str]:
        """Which axes the view may not turn about."""
        return frozenset(self._locked)

    def lock(self, axis: str, held: bool) -> None:
        """Hold one axis still, or let it go."""
        name = (axis or "").upper()[:1]
        if name not in AXES:
            return
        if held:
            self._locked.add(name)
        else:
            self._locked.discard(name)

        self._turning = False
        if self._locked:
            self._watch()
        else:
            self.stop()

    def stop(self) -> None:
        """Let go of the interactor."""
        interactor = self._interactor()
        for tag in self._observers:
            with contextlib.suppress(AttributeError, RuntimeError):
                interactor.RemoveObserver(tag)
        self._observers.clear()

    # ------------------------------------------------------------- the events

    def _interactor(self) -> Any:
        return self._plotter.iren.interactor

    def _watch(self) -> None:
        """Listen, once, and ahead of the camera controls.

        Below the drag handles, which sit at 10: a drag that grabbed a handle
        is moving the part and must not also turn the view.
        """
        if self._observers:
            return
        interactor = self._interactor()
        for event, handler in (
            ("LeftButtonPressEvent", self._pressed),
            ("MouseMoveEvent", self._moved),
            ("LeftButtonReleaseEvent", self._released),
        ):
            self._observers.append(interactor.AddObserver(event, handler, 5.0))

    def _claim(self, interactor: Any) -> None:
        """Take this event away from the camera controls."""
        for tag in self._observers:
            command = interactor.GetCommand(tag)
            if command is not None:
                command.SetAbortFlag(1)

    def _pressed(self, interactor: Any, _event: str) -> None:
        if not self._locked:
            return
        self._turning = True
        self._from = tuple(interactor.GetEventPosition())
        self._claim(interactor)

    def _moved(self, interactor: Any, _event: str) -> None:
        if not self._locked or not self._turning or self._from is None:
            return
        self._claim(interactor)

        now = tuple(interactor.GetEventPosition())
        across, up = now[0] - self._from[0], now[1] - self._from[1]
        if abs(across) < LEAST_PIXELS and abs(up) < LEAST_PIXELS:
            return
        self._from = now
        self.drag_by(across, up)

    def _released(self, interactor: Any, _event: str) -> None:
        if not self._locked:
            return
        self._turning = False
        self._from = None
        self._claim(interactor)

    # -------------------------------------------------------------- the maths

    def drag_by(self, across: int, up: int) -> None:
        """Turn the view as a drag of this size would, minus what is locked.

        Sideways asks to spin about the world's upright axis; up and down asks
        to tip about whichever way the camera calls right. Each is projected
        off the locked axes first, so a forbidden turn never happens at all.
        """
        moved = False

        for wanted, degrees in (
            (AXES["Z"], -across * DEGREES_PER_PIXEL),
            (self._camera_right(), -up * DEGREES_PER_PIXEL),
        ):
            if not degrees:
                continue
            axis = allowed_axis(wanted, self._locked)
            if axis is None:
                continue
            self._turn(axis, degrees)
            moved = True

        if moved:
            self._plotter.render()

    def _camera_right(self) -> NDArray[np.float64]:
        """Which way the camera calls right, in world terms."""
        camera = self._plotter.camera
        forward = np.array(camera.focal_point, dtype=np.float64) - np.array(
            camera.position, dtype=np.float64
        )
        right = np.cross(forward, np.array(camera.up, dtype=np.float64))
        length = float(np.linalg.norm(right))
        return AXES["X"] if length < LEAST_AXIS else right / length

    def _turn(self, axis: NDArray[np.float64], degrees: float) -> None:
        """Rotate the camera and its up vector about one axis.

        The up vector is turned rather than recomputed, which is what stops
        the horizon rolling: it stays exactly as square as it started.
        """
        camera = self._plotter.camera
        focus = np.array(camera.focal_point, dtype=np.float64)
        spin = turned_about(axis, degrees, focus)

        position = np.array(camera.position, dtype=np.float64)
        camera.position = tuple(spin[:3, :3] @ position + spin[:3, 3])
        camera.up = tuple(spin[:3, :3] @ np.array(camera.up, dtype=np.float64))
