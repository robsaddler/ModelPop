"""Turning the view, in the terms the mouse is held in.

The view turns like a turntable: dragging sideways spins the plate round,
dragging up and down raises and lowers the eye, and the horizon never rolls.
A trackball - what this used to do, and what VTK does by default - adds a third
freedom nobody asked for, and it is the one that arrives as "I tried to turn it
left and now it is skewed".

Either drag direction can be held still. **Named after the drag, not after the
axis**, which is the whole point: a first attempt offered X, Y and Z, and five
people testing it could not map those onto what their hand was doing. Holding
"up and down" is a sentence about the mouse; holding Y is a puzzle about the
world, and nobody should have to solve one to look at their model.

The arithmetic stays in world terms underneath, because that is what keeps the
horizon level: sideways turns about the world's upright axis rather than about
whatever the camera currently calls up, so it cannot accumulate roll.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = ["SIDEWAYS", "UP_AND_DOWN", "Turning", "turned_about"]

SIDEWAYS = "left-right"
UP_AND_DOWN = "up-down"

DIRECTIONS = (SIDEWAYS, UP_AND_DOWN)

# How far the view turns per pixel dragged. Fast enough to be useful, slow
# enough to stop where you meant.
DEGREES_PER_PIXEL = 0.4

# Below this a drag is a twitch rather than a turn.
LEAST_PIXELS = 1

# How close to straight up or straight down the eye may get. Going over the top
# turns the model upside down and swaps which way the mouse works, which reads
# as the view having broken.
NEAREST_THE_POLE = 4.0

_UPRIGHT = np.array([0.0, 0.0, 1.0])


def _above_the_horizon(away: NDArray[np.float64]) -> float:
    """How far above the plate the eye is, in degrees."""
    length = float(np.linalg.norm(away))
    if length < 1e-9:
        return 0.0
    return float(np.degrees(np.arcsin(np.clip(away[2] / length, -1.0, 1.0))))


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


class Turning:
    """Turntable turning, with either drag direction held still.

    Always listening, and ahead of the camera controls: the trackball is never
    allowed to run, because the roll it adds is the thing being designed out.
    Panning, the wheel and the drag handles are never claimed.
    """

    def __init__(self, plotter: Any) -> None:
        """Watch a plotter's interactor and take its turning over."""
        self._plotter = plotter
        self._held: set[str] = set()
        self._turning = False
        self._from: tuple[int, int] | None = None
        self._observers: list[int] = []
        self._watch()

    @property
    def held(self) -> frozenset[str]:
        """Which drag directions do nothing."""
        return frozenset(self._held)

    def hold(self, direction: str, held: bool) -> None:
        """Stop one drag direction turning the view, or let it go again."""
        if direction not in DIRECTIONS:
            return
        if held:
            self._held.add(direction)
        else:
            self._held.discard(direction)

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
        """Listen ahead of the camera controls.

        Below the drag handles, which sit at 10: a drag that grabbed a handle
        is moving the part and must not also turn the view.
        """
        if self._observers:
            return
        with contextlib.suppress(AttributeError, RuntimeError):
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
        self._turning = True
        self._from = tuple(interactor.GetEventPosition())
        self._claim(interactor)

    def _moved(self, interactor: Any, _event: str) -> None:
        if not self._turning or self._from is None:
            return
        self._claim(interactor)

        now = tuple(interactor.GetEventPosition())
        across, up = now[0] - self._from[0], now[1] - self._from[1]
        if abs(across) < LEAST_PIXELS and abs(up) < LEAST_PIXELS:
            return
        self._from = now
        self.drag_by(across, up)

    def _released(self, interactor: Any, _event: str) -> None:
        self._turning = False
        self._from = None
        self._claim(interactor)

    # -------------------------------------------------------------- the maths

    def drag_by(self, across: int, up: int) -> None:
        """Turn the view as a drag of this size would, minus what is held."""
        moved = False

        if across and SIDEWAYS not in self._held:
            # About the world's upright axis, never about the camera's own -
            # which is exactly what stops roll accumulating.
            self._turn(_UPRIGHT, -across * DEGREES_PER_PIXEL)
            moved = True

        if up and UP_AND_DOWN not in self._held:
            moved = self._tilt(-up * DEGREES_PER_PIXEL) or moved

        if moved:
            self._plotter.render()

    def _camera_right(self) -> NDArray[np.float64]:
        """Which way the camera calls right, in world terms."""
        camera = self._plotter.camera
        forward = np.array(camera.focal_point, dtype=np.float64) - np.array(
            camera.position, dtype=np.float64
        )
        right = np.cross(forward, _UPRIGHT)
        length = float(np.linalg.norm(right))
        if length < 1e-9:
            # Looking straight down the upright axis: any horizontal direction
            # will do, and X is as good as any.
            return np.array([1.0, 0.0, 0.0])
        return right / length

    def _tilt(self, degrees: float) -> bool:
        """Raise or lower the eye, stopping short of straight up or down.

        The limit is checked by working out where the camera *would* end up
        and refusing to go there, rather than by reasoning about which way a
        positive angle turns. The first version did the reasoning and got the
        sign backwards, so forty hard upward drags went over the top and came
        out at seventy-nine degrees below - upside down, with the mouse
        working backwards.
        """
        camera = self._plotter.camera
        focus = np.array(camera.focal_point, dtype=np.float64)
        position = np.array(camera.position, dtype=np.float64)

        spin = turned_about(self._camera_right(), degrees, focus)
        would_be = spin[:3, :3] @ position + spin[:3, 3]
        if abs(_above_the_horizon(would_be - focus)) > 90.0 - NEAREST_THE_POLE:
            return False

        camera.position = tuple(would_be)
        camera.up = tuple(spin[:3, :3] @ np.array(camera.up, dtype=np.float64))
        return True

    def _turn(self, axis: NDArray[np.float64], degrees: float) -> None:
        """Rotate the camera about one world axis through what it looks at.

        The up vector is turned with it rather than recomputed, so it stays
        exactly as square to the world as it started.
        """
        camera = self._plotter.camera
        focus = np.array(camera.focal_point, dtype=np.float64)
        spin = turned_about(axis, degrees, focus)

        position = np.array(camera.position, dtype=np.float64)
        camera.position = tuple(spin[:3, :3] @ position + spin[:3, 3])
        camera.up = tuple(spin[:3, :3] @ np.array(camera.up, dtype=np.float64))
