"""Taking hold of a face and moving it - SketchUp's push/pull.

The operation people mean when they say they want to *model* rather than to
configure something. Everything else in this application's vocabulary makes a
shape out of numbers; this one changes a shape that is already there by
pointing at part of it and pulling.

Three things have to work for it to feel like anything:

* **The face lights up before you commit.** A flat surface on a tessellated
  solid is many triangles, so the ones lying in the same plane and joined to
  the one under the cursor are gathered and drawn as a sheet. Without that you
  are aiming at a triangle nobody can see the edges of.
* **It moves along its own normal**, not along an axis. That is what makes it
  read the same whichever way the part has been turned, and it is the whole
  difference between push/pull and a move.
* **The cursor stays on it.** The drag is the closest approach of the mouse
  ray to the face's normal line, which is exact at any zoom - the same
  arithmetic the drag handles use, and for the same reason.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np
import pyvista as pv

from modelpop.rendering.drag_handles import along_axis

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = ["FacePull", "coplanar_with"]

# How nearly parallel two triangles must be to count as the same flat face.
# About four degrees, which is loose enough for a tessellated cylinder's flat
# ends and tight enough not to swallow the curved side.
SAME_PLANE = 0.997

# And how nearly they must lie in the same plane rather than a parallel one, in
# millimetres. Two faces of a thin wall are parallel and are not the same face.
SAME_OFFSET_MM = 0.05

# The face under the cursor, drawn over it. Warm against the model's blue, and
# translucent so the shape of the part still reads through it.
FACE_COLOUR = "#F2C14E"
FACE_OPACITY = 0.45

# The arrow that says which way it will move, as a fraction of the face's size.
ARROW_LENGTH = 0.55
ARROW_SHAFT = 0.04
ARROW_TIP = 0.13
ARROW_COLOUR = "#F2C14E"

# Below this a face is too small to draw an arrow on sensibly, in millimetres.
LEAST_FACE_MM = 0.5


def coplanar_with(
    triangle: int, vertices: NDArray[np.float64], faces: NDArray[np.integer]
) -> tuple[NDArray[np.intp], NDArray[np.float64]]:
    """Every triangle on the same flat face as this one, and that face's normal.

    Grown outwards from the triangle under the cursor rather than taken as
    "everything with this normal": the top and bottom of a plate are parallel
    and are emphatically not the same face, so a candidate has to be both
    parallel *and* at the same offset along that normal.

    Connectivity is deliberately not used. Walking the edge graph would be
    stricter and is what a kernel does, but it costs a neighbour map over every
    triangle, and on a flat face the plane test alone gives the same answer for
    a fraction of the work.
    """
    corners = vertices[faces]
    edges_a = corners[:, 1] - corners[:, 0]
    edges_b = corners[:, 2] - corners[:, 0]
    normals = np.cross(edges_a, edges_b)
    lengths = np.linalg.norm(normals, axis=1)
    good = lengths > 1e-12
    normals[good] /= lengths[good, None]

    wanted = normals[triangle]
    offset = float(np.dot(corners[triangle, 0], wanted))

    parallel = normals @ wanted > SAME_PLANE
    in_plane = np.abs(corners[:, 0] @ wanted - offset) < SAME_OFFSET_MM
    return np.flatnonzero(parallel & in_plane), wanted


class FacePull:
    """Hover a face, take hold of it, and move it along its own normal.

    Draws nothing until a face is under the cursor, and owns no state the
    viewport needs to know about beyond "is something being pulled right now".
    """

    def __init__(
        self,
        plotter: Any,
        pick: Callable[[float, float], tuple[tuple[float, ...], int] | None],
        geometry: Callable[[], tuple[NDArray[np.float64], NDArray[np.integer]] | None],
        on_release: Callable[[tuple[float, float, float], float], None],
        on_move: Callable[[float], None] | None = None,
    ) -> None:
        """Watch a plotter and report what a pull came to.

        Args:
            plotter: the PyVista plotter to draw on.
            pick: turns a point on screen into a hit and the triangle it hit.
            geometry: the vertices and faces currently drawn, or ``None``.
            on_release: given the point taken hold of and how far it moved.
            on_move: told the distance while it is still moving.
        """
        self._plotter = plotter
        self._pick = pick
        self._geometry = geometry
        self._on_release = on_release
        self._on_move = on_move

        self._observers: list[int] = []
        self._sheet: Any = None
        self._arrow: Any = None
        self._at: NDArray[np.float64] | None = None
        self._normal: NDArray[np.float64] | None = None
        self._size = 1.0
        self._started_at: float | None = None
        self._pulling = False
        self._watch()

    @property
    def is_pulling(self) -> bool:
        """Whether a face is being moved right now."""
        return self._pulling

    def stop(self) -> None:
        """Let go of the interactor and take the overlay off."""
        interactor = self._interactor()
        for tag in self._observers:
            with contextlib.suppress(AttributeError, RuntimeError):
                interactor.RemoveObserver(tag)
        self._observers.clear()
        self._forget_the_face()

    # ------------------------------------------------------------- the events

    def _interactor(self) -> Any:
        return self._plotter.iren.interactor

    def _watch(self) -> None:
        """Listen ahead of the camera controls, as the drag handles do.

        At the same priority: only one of the two is ever switched on, so they
        never compete, and both have to beat the turntable underneath.
        """
        with contextlib.suppress(AttributeError, RuntimeError):
            interactor = self._interactor()
            for event, handler in (
                ("MouseMoveEvent", self._moved),
                ("LeftButtonPressEvent", self._pressed),
                ("LeftButtonReleaseEvent", self._released),
            ):
                self._observers.append(interactor.AddObserver(event, handler, 10.0))

    def _claim(self, interactor: Any) -> None:
        """Keep a pull from also orbiting the view."""
        for tag in self._observers:
            command = interactor.GetCommand(tag)
            if command is not None:
                command.SetAbortFlag(1)

    def _pressed(self, interactor: Any, _event: str) -> None:
        if self._at is None or self._normal is None:
            return
        reading = self._reading(interactor)
        if reading is None:
            return
        self._pulling = True
        self._started_at = reading
        self._claim(interactor)

    def _moved(self, interactor: Any, _event: str) -> None:
        if not self._pulling:
            self._hover(interactor)
            return

        self._claim(interactor)
        reading = self._reading(interactor)
        if reading is None or self._started_at is None:
            return
        moved = reading - self._started_at
        self._show_the_arrow(moved)
        self._plotter.render()
        if self._on_move is not None:
            self._on_move(moved)

    def _released(self, interactor: Any, _event: str) -> None:
        if not self._pulling or self._at is None:
            return
        self._claim(interactor)
        self._pulling = False

        reading = self._reading(interactor)
        moved = 0.0 if reading is None or self._started_at is None else reading - self._started_at
        at = (float(self._at[0]), float(self._at[1]), float(self._at[2]))
        self._started_at = None
        self._on_release(at, moved)

    # -------------------------------------------------------------- the face

    def _hover(self, interactor: Any) -> None:
        """Light up whatever flat face the cursor is over."""
        x, y = interactor.GetEventPosition()
        hit = self._pick(float(x), float(y))
        drawn = self._geometry()
        if hit is None or drawn is None:
            self._forget_the_face()
            return

        point, triangle = hit
        vertices, faces = drawn
        if triangle < 0 or triangle >= len(faces):
            self._forget_the_face()
            return

        on_it, normal = coplanar_with(triangle, vertices, faces)
        self._show_the_face(vertices, faces[on_it], np.array(point[:3]), normal)

    def _show_the_face(
        self,
        vertices: NDArray[np.float64],
        faces: NDArray[np.integer],
        at: NDArray[np.float64],
        normal: NDArray[np.float64],
    ) -> None:
        """Draw the face as a sheet, with an arrow saying which way it goes."""
        self._at = at
        self._normal = normal
        corners = vertices[faces]
        across = np.ptp(corners.reshape(-1, 3), axis=0)
        self._size = max(float(np.linalg.norm(across)), LEAST_FACE_MM)

        grid = np.empty((len(faces), 4), dtype=np.int64)
        grid[:, 0] = 3
        grid[:, 1:] = faces
        sheet = pv.PolyData(np.ascontiguousarray(vertices, dtype=np.float64), grid.ravel())
        with contextlib.suppress(AttributeError, RuntimeError, ValueError):
            self._sheet = self._plotter.add_mesh(
                sheet,
                color=FACE_COLOUR,
                opacity=FACE_OPACITY,
                lighting=False,
                name="pull-face",
                render=False,
            )
        self._show_the_arrow(0.0)
        self._plotter.render()

    def _show_the_arrow(self, moved: float) -> None:
        """An arrow from the face, along the way it would go."""
        if self._at is None or self._normal is None:
            return
        length = max(self._size * ARROW_LENGTH, abs(moved))
        pointing = self._normal if moved >= 0 else -self._normal
        arrow = pv.Arrow(
            start=self._at + self._normal * moved,
            direction=pointing,
            scale=length,
            shaft_radius=ARROW_SHAFT * self._size / length,
            tip_radius=ARROW_TIP * self._size / length,
            tip_length=min(0.35, ARROW_TIP * self._size * 3.0 / length),
        )
        with contextlib.suppress(AttributeError, RuntimeError, ValueError):
            self._arrow = self._plotter.add_mesh(
                arrow, color=ARROW_COLOUR, lighting=False, name="pull-arrow", render=False
            )

    def _forget_the_face(self) -> None:
        """Take the overlay off, because nothing is under the cursor."""
        if self._at is None and self._sheet is None:
            return
        self._at = None
        self._normal = None
        for name in ("pull-face", "pull-arrow"):
            with contextlib.suppress(AttributeError, RuntimeError, ValueError):
                self._plotter.remove_actor(name, render=False)
        self._sheet = None
        self._arrow = None
        with contextlib.suppress(AttributeError, RuntimeError):
            self._plotter.render()

    # -------------------------------------------------------------- the maths

    def _reading(self, interactor: Any) -> float | None:
        """How far along the face's normal the cursor currently is.

        The closest approach of the mouse ray to the normal line through the
        point that was taken hold of - exact at any zoom, which is the whole
        reason the drag handles were rewritten to work this way.
        """
        if self._at is None or self._normal is None:
            return None
        ray = self._ray(interactor)
        if ray is None:
            return None
        return along_axis(self._at, self._normal, *ray)

    def _ray(self, interactor: Any) -> tuple[NDArray[np.float64], NDArray[np.float64]] | None:
        """The line through the scene under the cursor, in world terms."""
        import vtk

        renderer = getattr(self._plotter, "renderer", None)
        if renderer is None:
            return None
        x, y = interactor.GetEventPosition()

        points = []
        for depth in (0.0, 1.0):
            at = vtk.vtkCoordinate()
            at.SetCoordinateSystemToDisplay()
            at.SetValue(float(x), float(y), depth)
            points.append(np.array(at.GetComputedWorldValue(renderer), dtype=np.float64))

        along = points[1] - points[0]
        length = float(np.linalg.norm(along))
        if length < 1e-9:
            return None
        return points[0], along / length
