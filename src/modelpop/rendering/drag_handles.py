"""Drag handles that follow the cursor exactly.

Written to replace PyVista's ``AffineWidget3D``, which was reported as unusable
and is - for three reasons visible in its own source:

1. Its translation is documented as "not physically accurate" and "ignores
   zoom". It converts the cursor to world space and then multiplies by the
   actor's length times two, so how far a part moves per pixel depends on how
   big the part is and not at all on how far away the camera is. Zoomed in on a
   small part, the mouse has to travel the width of the desk - twice - to shift
   it a few millimetres.
2. Its handle actors are given a transform once, when they are built, and never
   again. The part moves during a drag and the handles stay where they were, so
   the gizmo visibly detaches from the thing it is moving.
3. ``always_visible`` draws the handles with a polygon offset of -20000, which
   is what smears them over the model.

What replaces it does the ordinary thing instead. A drag along an arrow solves
for the point on that axis closest to the cursor's ray through the scene, so the
handle stays under the pointer exactly, at any zoom, on any size of part. A drag
on a ring intersects the same ray with the ring's plane and measures the angle.
Both are a few lines of vector algebra and neither needs a fudge factor.

The handles are anchored to the *outside* of the part's bounding box rather than
to its centre, so no arrow is ever buried inside the geometry it moves, and
ordinary depth testing can be left alone.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import numpy as np
import pyvista as pv

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray

__all__ = ["AXIS_COLOURS", "HIGHLIGHT_COLOUR", "DragHandles", "along_axis", "on_plane"]

# Red, green, blue for X, Y and Z - the convention every CAD package shares, so
# nobody has to learn ours. Muted for a dark viewport.
AXIS_COLOURS = ("#E06C75", "#98C379", "#61AFEF")

# The same yellow the measuring tool uses, so "this is the thing you grabbed"
# always looks the same.
HIGHLIGHT_COLOUR = "#F2C14E"

# All as a fraction of the part's longest dimension.
GAP = 0.08
"""How far clear of the bounding box an arrow starts."""

LEAST_ARM = 0.22
"""The shortest an arrow may be, for a part far longer in one direction."""

ARROWS_WITHIN = 0.78
"""Where the arrowheads finish, as a fraction of the ring radius."""

SHAFT = 0.011
"""Arrow shaft radius, as a fraction of the part. Thin: the handles are for
aiming at, not for looking at - and the picker is far more generous than they
look, so there is no need to make them fat to make them hittable."""

TIP = 0.036
"""Arrow head radius, as a fraction of the part."""

RING = 0.007
"""Rotation ring tube radius, as a fraction of the part."""

RESTING_OPACITY = 0.18
"""How faint the handles go while they cannot be grabbed."""

RING_OPACITY = 0.45
"""Rings sit back so the arrows read first. Moving is much the commoner intent,
and three full-strength circles round a small part is what got the old handles
called ugly. A ring comes up to full strength when the cursor is on it."""

RING_LEAST = 0.6
RING_MOST = 3.0
"""Bounds on how far the rotation rings reach, as a fraction of the part."""

RING_MARGIN = 1.22
"""How far outside the part the rings sit. The arrows finish inside them, so a
click meant for an arrow can never land on a ring."""

# A drag that starts within this many pixels of a handle counts as being on it.
# Generous, because a thin arrow is hard to hit exactly and the alternative -
# clicking and nothing happening - is the complaint this whole module exists to
# answer.
PICK_TOLERANCE = 0.01

_AXES: tuple[NDArray[np.float64], ...] = (
    np.array([1.0, 0.0, 0.0]),
    np.array([0.0, 1.0, 0.0]),
    np.array([0.0, 0.0, 1.0]),
)


def along_axis(
    anchor: NDArray[np.float64],
    axis: NDArray[np.float64],
    ray_from: NDArray[np.float64],
    ray_along: NDArray[np.float64],
) -> float | None:
    """How far along ``axis`` the cursor's ray comes closest to it.

    The standard closest-approach of two skew lines. This is what makes a drag
    track the pointer exactly: the distance returned is in millimetres of the
    scene, derived from where the ray actually goes, so it is correct at any
    zoom and for any size of part.

    Returns ``None`` when the camera is looking almost straight down the axis,
    where the answer is unbounded - the two lines are nearly parallel and a
    pixel of mouse movement would mean metres. Refusing is the honest outcome;
    the part simply does not move until the view is turned.

    Args:
        anchor: any point on the axis.
        axis: unit vector along it.
        ray_from: where the cursor's ray starts, in world space.
        ray_along: unit vector along that ray.
    """
    between = anchor - ray_from
    dot = float(np.dot(axis, ray_along))
    denominator = 1.0 - dot * dot
    if abs(denominator) < 1e-6:
        return None
    return float((dot * np.dot(ray_along, between) - np.dot(axis, between)) / denominator)


def on_plane(
    centre: NDArray[np.float64],
    normal: NDArray[np.float64],
    ray_from: NDArray[np.float64],
    ray_along: NDArray[np.float64],
) -> NDArray[np.float64] | None:
    """Where the cursor's ray crosses the plane a rotation ring lies in.

    ``None`` when the ray runs along the plane rather than through it, which is
    the ring seen edge-on: there is no meaningful angle to read from it.
    """
    facing = float(np.dot(ray_along, normal))
    if abs(facing) < 1e-6:
        return None
    distance = float(np.dot(centre - ray_from, normal)) / facing
    return ray_from + distance * ray_along


class DragHandles:
    """Arrows and rings on a part, and what dragging them means.

    Owns its actors, its observers and the transform it has built up so far.
    ``stop`` puts everything back.
    """

    def __init__(
        self,
        plotter: Any,
        actor: Any,
        bounds: tuple[float, float, float, float, float, float],
        on_release: Callable[[Any], None],
        on_move: Callable[[Any], None] | None = None,
    ) -> None:
        """Put handles on a part.

        Args:
            plotter: the PyVista plotter the part is drawn in.
            actor: the part's actor, which is what gets transformed.
            bounds: the part's bounding box, as VTK orders it.
            on_release: told the final transform when the button comes up.
            on_move: told the transform as it changes, so something can report
                what is happening while it is still happening.
        """
        self._plotter = plotter
        self._on_release = on_release
        self._on_move = on_move

        # A Rotate feature compiles to build123d's ``Rot``, which turns the
        # part about the **world origin**. So that is what the rings turn
        # about too. Pivoting the preview on the part's own centre instead -
        # which is what the widget this replaces did - makes the gizmo show
        # one thing and the rebuild do another, and quietly emits a spurious
        # Move alongside the Rotate to make the arithmetic come out.
        self._pivot = np.zeros(3, dtype=np.float64)

        self._arrows: list[Any] = []
        self._rings: list[Any] = []
        self._held: Any = None
        self._hovered: Any = None
        self._matrix = np.eye(4)
        self._started_at: float | None = None
        self._observers: list[int] = []
        self._active = True
        self._arrow_picker: Any = None
        self._ring_picker: Any = None

        self.attach(actor, bounds)
        self._watch()

    # ------------------------------------------------------- staying on screen

    def attach(
        self,
        actor: Any,
        bounds: tuple[float, float, float, float, float, float],
    ) -> None:
        """Point the handles at a part, replacing whatever they were on.

        Every rebuild makes a new actor, and the part it draws may be a
        different size and in a different place - so the handles have to be
        re-made around it. They are replaced **by name**, which swaps the
        geometry inside the existing actors rather than removing them and
        adding new ones.

        That distinction is the whole point of this method. Taking them out of
        the scene and putting them back is what made them blink out for the
        length of a rebuild, which was reported - reasonably - as the controls
        disappearing. They now stay on screen from the moment they are turned
        on until they are turned off.
        """
        self._actor = actor
        low = np.array(bounds[0::2], dtype=np.float64)
        high = np.array(bounds[1::2], dtype=np.float64)
        self._centre = (low + high) / 2.0
        self._size = float(max(high - low)) or 1.0

        self._arrows = []
        self._rings = []
        self._build(low, high)

        # The pickers hold actor references, so they are remade with them.
        # Two of them, so that moving always beats turning where both are
        # under the cursor: grabbing a ring when you aimed at an arrow is the
        # kind of thing that gets a gizmo called broken.
        self._arrow_picker = self._make_picker(self._arrows)
        self._ring_picker = self._make_picker(self._rings)

        self._held = None
        self._hovered = None
        self._started_at = None
        self._matrix = np.eye(4)
        self.set_active(True)

    def set_active(self, active: bool) -> None:
        """Whether the handles can be grabbed.

        They stay visible either way. While a released drag is being turned
        into features the part is already where it was put but the feature
        tree has not caught up, and a second drag started against that state
        is refused outright by the command bus and silently lost. So they go
        quiet instead of going away - dimmed, because handles that look live
        and ignore you are worse than handles that say they are not ready.
        """
        self._active = active
        for handle in self._arrows:
            handle.prop.opacity = 1.0 if active else RESTING_OPACITY
        for handle in self._rings:
            handle.prop.opacity = RING_OPACITY if active else RESTING_OPACITY

    @property
    def is_active(self) -> bool:
        """Whether a grab would be accepted."""
        return self._active

    # --------------------------------------------------------------- the look

    def _build(self, low: NDArray[np.float64], high: NDArray[np.float64]) -> None:
        """Three arrows and three rings, all clear of the part itself.

        Laid out so the three arrowheads land on one radius and the rings sit
        just outside them. Each arrow starts at its own face of the bounding
        box, so none is ever buried in the geometry, and each is therefore a
        different length - which is what makes them all finish together.
        """
        gap = self._size * GAP
        corners = np.array(
            [
                [x, y, z]
                for x in (low[0], high[0])
                for y in (low[1], high[1])
                for z in (low[2], high[2])
            ]
        )
        # Enclosing the part from the pivot, with room to spare. Capped,
        # because a part parked a long way from the origin would otherwise get
        # a gizmo the size of the room; what has to be truthful is where the
        # rings are centred, not how far out they reach.
        reach = float(np.max(np.linalg.norm(corners - self._pivot, axis=1))) + gap
        ring_radius = float(
            np.clip(reach * RING_MARGIN, RING_LEAST * self._size, RING_MOST * self._size)
        )
        tips_at = ring_radius * ARROWS_WITHIN

        for index, axis in enumerate(_AXES):
            starts_at = float(high[index] - self._pivot[index]) + gap
            arm = max(tips_at - starts_at, self._size * LEAST_ARM)
            arrow = pv.Arrow(
                start=self._pivot + axis * starts_at,
                direction=axis,
                scale=arm,
                shaft_radius=SHAFT * self._size / arm,
                tip_radius=TIP * self._size / arm,
                tip_length=min(0.35, TIP * self._size * 3.0 / arm),
            )
            self._arrows.append(
                self._plotter.add_mesh(
                    arrow,
                    color=AXIS_COLOURS[index],
                    lighting=False,
                    name=f"drag-arrow-{index}",
                    render=False,
                )
            )
            self._rings.append(
                self._plotter.add_mesh(
                    _ring(self._pivot, index, ring_radius, self._size * RING),
                    color=AXIS_COLOURS[index],
                    lighting=False,
                    opacity=RING_OPACITY,
                    name=f"drag-ring-{index}",
                    render=False,
                )
            )

    def _make_picker(self, only: list[Any]) -> Any:
        """A picker that can see the given handles and nothing else.

        Restricting the pick list is what lets an arrow be grabbed even where
        it passes close to the part, and means the part itself can never be
        picked up by accident. It also removes any need for the depth-offset
        trick that smeared the old handles across the model.
        """
        from vtkmodules.vtkRenderingCore import vtkCellPicker

        picker = vtkCellPicker()
        picker.SetTolerance(PICK_TOLERANCE)
        picker.InitializePickList()
        for handle in only:
            picker.AddPickList(handle)
        picker.PickFromListOn()
        return picker

    @property
    def _handles(self) -> list[Any]:
        return [*self._arrows, *self._rings]

    # ----------------------------------------------------------- the plumbing

    def _watch(self) -> None:
        """Listen ahead of the camera controls.

        Priority matters: the interactor style turns a left-button drag into an
        orbit, so a grab has to be seen first and then stopped from reaching it.
        Otherwise the part and the camera move together.
        """
        interactor = self._plotter.iren.interactor
        for event, handler in (
            ("MouseMoveEvent", self._moved),
            ("LeftButtonPressEvent", self._pressed),
            ("LeftButtonReleaseEvent", self._released),
        ):
            self._observers.append(interactor.AddObserver(event, handler, 10.0))

    def reset(self) -> None:
        """Forget the transform built up so far, and put the handles back.

        Called when the part is put back where it started. Without it the next
        drag begins from the last one's total: the handles jump the moment
        they are touched and the part moves twice as far as it was asked to.
        """
        self._matrix = np.eye(4)
        for handle in self._handles:
            with contextlib.suppress(AttributeError, RuntimeError, ValueError):
                handle.user_matrix = np.eye(4)

    def stop(self) -> None:
        """Take the handles off and stop listening."""
        interactor = self._plotter.iren.interactor
        for tag in self._observers:
            with contextlib.suppress(AttributeError, RuntimeError):
                interactor.RemoveObserver(tag)
        self._observers.clear()
        for handle in self._handles:
            with contextlib.suppress(AttributeError, RuntimeError, ValueError):
                self._plotter.remove_actor(handle, render=False)
        self._arrows.clear()
        self._rings.clear()

    def _stop_the_camera(self, interactor: Any) -> None:
        """Keep a grab from also orbiting the view."""
        for tag in self._observers:
            command = interactor.GetCommand(tag)
            if command is not None:
                command.SetAbortFlag(1)

    # ------------------------------------------------------------ the pointer

    def _ray(self, interactor: Any) -> tuple[NDArray[np.float64], NDArray[np.float64]] | None:
        """The line through the scene under the cursor, in world space.

        Taken from the renderer's own projection, so it is correct under
        perspective and at any zoom - which is the whole difference between
        this and what it replaces.
        """
        x, y = interactor.GetEventPosition()
        renderer = self._plotter.renderer

        renderer.SetDisplayPoint(float(x), float(y), 0.0)
        renderer.DisplayToWorld()
        near = np.array(renderer.GetWorldPoint(), dtype=np.float64)
        renderer.SetDisplayPoint(float(x), float(y), 1.0)
        renderer.DisplayToWorld()
        far = np.array(renderer.GetWorldPoint(), dtype=np.float64)
        if near[3] == 0.0 or far[3] == 0.0:
            return None

        start = near[:3] / near[3]
        finish = far[:3] / far[3]
        length = float(np.linalg.norm(finish - start))
        if length < 1e-9:
            return None
        return start, (finish - start) / length

    def _handle_under(self, interactor: Any) -> Any:
        """Whichever handle the cursor is over, or ``None``.

        Arrows are asked about first. Moving is much the commoner intent, and
        where a ring crosses an arrow on screen the arrow is what was aimed at.
        """
        x, y = interactor.GetEventPosition()
        for picker, group in (
            (self._arrow_picker, self._arrows),
            (self._ring_picker, self._rings),
        ):
            picker.Pick(float(x), float(y), 0.0, self._plotter.renderer)
            picked = picker.GetActor()
            if picked in group:
                return picked
        return None

    # ------------------------------------------------------------- the events

    def _pressed(self, interactor: Any, _event: str) -> None:
        """Grab whatever is under the cursor, if it is one of ours."""
        if not self._active:
            return
        handle = self._handle_under(interactor)
        if handle is None:
            return

        ray = self._ray(interactor)
        if ray is None:
            return

        reading = self._read(handle, *ray)
        if reading is None:
            return

        self._held = handle
        self._started_at = reading
        self._highlight(handle)
        self._stop_the_camera(interactor)

    def _moved(self, interactor: Any, _event: str) -> None:
        """Track the cursor if a handle is held, otherwise just light one up."""
        if self._held is None:
            self._hover(self._handle_under(interactor) if self._active else None)
            return

        self._stop_the_camera(interactor)
        ray = self._ray(interactor)
        if ray is None or self._started_at is None:
            return

        reading = self._read(self._held, *ray)
        if reading is None:
            return

        self._apply(self._step(self._held, reading - self._started_at))
        if self._on_move is not None:
            self._on_move(self._actor.user_matrix)

    def _released(self, interactor: Any, _event: str) -> None:
        """Let go, and say what the whole drag came to."""
        if self._held is None:
            return

        self._stop_the_camera(interactor)
        self._unhighlight(self._held)
        self._held = None
        self._started_at = None
        self._matrix = np.asarray(self._actor.user_matrix, dtype=np.float64).copy()
        self._on_release(self._actor.user_matrix)

    # -------------------------------------------------------------- the maths

    def _read(
        self, handle: Any, ray_from: NDArray[np.float64], ray_along: NDArray[np.float64]
    ) -> float | None:
        """Where the cursor is, in the one number this handle cares about.

        Millimetres along an arrow's axis, or radians around a ring. Both are
        absolute, so a drag is the difference between two of them and nothing
        accumulates error.
        """
        if handle in self._arrows:
            index = self._arrows.index(handle)
            return along_axis(self._centre, _AXES[index], ray_from, ray_along)

        index = self._rings.index(handle)
        normal = _AXES[index]
        hit = on_plane(self._pivot, normal, ray_from, ray_along)
        if hit is None:
            return None
        across, up = _AXES[(index + 1) % 3], _AXES[(index + 2) % 3]
        spoke = hit - self._pivot
        return float(np.arctan2(np.dot(spoke, up), np.dot(spoke, across)))

    def _step(self, handle: Any, moved: float) -> NDArray[np.float64]:
        """The transform this drag has produced, from where it started."""
        if handle in self._arrows:
            index = self._arrows.index(handle)
            step = np.eye(4)
            step[:3, 3] = _AXES[index] * moved
            return step

        index = self._rings.index(handle)
        return _turn_about(self._pivot, _AXES[index], moved)

    def _apply(self, step: NDArray[np.float64]) -> None:
        """Move the part, and move the handles with it.

        The second half is the one the old widget left out. Handles that stay
        behind while the part slides away do not read as a gizmo at all; they
        read as the screen failing to repaint.
        """
        matrix = step @ self._matrix
        self._actor.user_matrix = matrix
        for handle in self._handles:
            handle.user_matrix = matrix
        self._plotter.render()

    # ---------------------------------------------------------- the highlight

    def _hover(self, handle: Any) -> None:
        """Light up whatever the cursor is over, and put back whatever it left."""
        if handle is self._hovered:
            return
        if self._hovered is not None:
            self._unhighlight(self._hovered)
        self._hovered = handle
        if handle is not None:
            self._highlight(handle)
        self._plotter.render()

    def _highlight(self, handle: Any) -> None:
        handle.prop.color = HIGHLIGHT_COLOUR
        handle.prop.opacity = 1.0

    def _unhighlight(self, handle: Any) -> None:
        index = (self._arrows + self._rings).index(handle) % 3
        handle.prop.color = AXIS_COLOURS[index]
        if not self._active:
            handle.prop.opacity = RESTING_OPACITY
        else:
            handle.prop.opacity = 1.0 if handle in self._arrows else RING_OPACITY


def _ring(centre: NDArray[np.float64], index: int, radius: float, thickness: float) -> pv.PolyData:
    """A circle about one axis, as a tube.

    Built from its own points rather than by filtering a disc, because what is
    wanted here is a closed line and a disc that has had its interior removed
    is only incidentally one.
    """
    across, up = _AXES[(index + 1) % 3], _AXES[(index + 2) % 3]
    angles = np.linspace(0.0, 2.0 * np.pi, 96, endpoint=False)
    points = (
        centre + radius * np.outer(np.cos(angles), across) + radius * np.outer(np.sin(angles), up)
    )
    # A closed polyline: the last point joins back to the first.
    closed = np.vstack([points, points[:1]])
    line = pv.PolyData(closed)
    line.lines = np.hstack([[len(closed)], np.arange(len(closed))])
    tube: pv.PolyData = line.tube(radius=thickness, n_sides=12)
    return tube


def _turn_about(
    centre: NDArray[np.float64], axis: NDArray[np.float64], radians: float
) -> NDArray[np.float64]:
    """A rotation about an axis through a point, as a 4x4."""
    cosine, sine = np.cos(radians), np.sin(radians)
    x, y, z = axis
    rotation = np.array(
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
    matrix[:3, :3] = rotation
    matrix[:3, 3] = centre - rotation @ centre
    return matrix
