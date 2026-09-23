"""The 3D viewport, built on PyVista and VTK.

Sits above the presentation layer, not below it: a viewport is a UI concern and
drags in Qt through ``pyvistaqt``. View-models must stay drivable without any of
it, and an ``import-linter`` contract enforces that.

Measured in spike S7 (``docs/research/spike-s7-python-stack.md``): 983k triangles
at 28-36 FPS and ray picking in 0.0045 ms with a cached ``vtkCellLocator``. Note
that those figures were taken on the **integrated** GPU, so they are a floor.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import pyvista as pv

from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.units import Unit
from modelpop.presentation.sectioning import SectionPlane

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = [
    "BUILD_PLATE_COLOUR",
    "INTERIOR_COLOUR",
    "MEASURE_COLOUR",
    "MODEL_COLOUR",
    "NO_RENDERER",
    "PROBLEM_COLOUR",
    "PickResult",
    "ViewportScene",
    "renderer_in",
    "to_polydata",
]

# A picked point in millimetres, and the index of the triangle that was hit.
PickResult = tuple[tuple[float, ...], int]

# Chosen to read clearly against a dark viewport, and to keep the problem
# colour distinguishable from the model colour for the red-green colour blind:
# the two differ in lightness as well as hue.
MODEL_COLOUR = "#6FA8DC"
PROBLEM_COLOUR = "#E8834A"
BUILD_PLATE_COLOUR = "#3A4750"
ENVELOPE_COLOUR = "#8899A6"
MEASURE_COLOUR = "#F2C14E"

# Big enough to see against a model, small enough not to hide the feature
# being measured. In millimetres, because everything here is.
MEASURE_POINT_MM = 0.8
# The colour of a cut surface. Warm against the model's blue, so the inside
# of a sectioned part is unmistakably the inside.
INTERIOR_COLOUR = "#C9A227"

BACKGROUND_TOP = "#2B3038"
BACKGROUND_BOTTOM = "#171A1F"


def _as_fraction(colour: str) -> tuple[float, float, float]:
    """A hex colour as the three fractions VTK wants."""
    value = colour.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


# What to say when the driver will not answer - which includes every off-screen
# window, because one has no device context at all.
NO_RENDERER = "The graphics driver did not say what is drawing."


def renderer_in(report: str) -> str:
    """The graphics card named in an OpenGL capability report.

    A pure function over the driver's own text, so every shape of report -
    including the empty one an off-screen window gives - is testable without a
    graphics context.
    """
    found: dict[str, str] = {}
    for line in report.splitlines():
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key in {"opengl vendor string", "opengl renderer string"}:
            found[key] = value.strip()

    renderer = found.get("opengl renderer string")
    if not renderer:
        return NO_RENDERER
    vendor = found.get("opengl vendor string", "")
    return f"Drawing on {renderer}" + (f" ({vendor})" if vendor else "")


def to_polydata(mesh: Mesh) -> pv.PolyData:
    """Convert a domain mesh into something VTK can draw.

    VTK wants faces as a flat array prefixed with the vertex count per face, so
    a triangle mesh becomes ``[3, a, b, c, 3, d, e, f, ...]``.

    Coordinates are converted to millimetres so the build plate and the model
    always share one coordinate system. A model silently drawn in inches next to
    a plate drawn in millimetres looks like a scale bug and is impossible to
    diagnose by eye.
    """
    in_mm = mesh.to_unit(Unit.MILLIMETRE)
    if in_mm.is_empty:
        return pv.PolyData()

    faces: NDArray[np.int64] = np.empty((in_mm.triangle_count, 4), dtype=np.int64)
    faces[:, 0] = 3
    faces[:, 1:] = in_mm.faces
    return pv.PolyData(np.ascontiguousarray(in_mm.vertices, dtype=np.float64), faces.ravel())


class ViewportScene:
    """What is drawn, kept separate from how it is displayed.

    Holds no widget, so it can be exercised in an off-screen plotter by a test
    and reused by whatever window eventually hosts it.
    """

    def __init__(self, plotter: Any, printer: PrinterProfile | None = None) -> None:
        """Attach the scene to a plotter.

        Args:
            plotter: any PyVista plotter - on-screen, off-screen or embedded.
            printer: the printer whose build volume to draw.
        """
        self._plotter = plotter
        self._printer = printer or PrinterProfile.p2s()
        self._model_actor: Any = None
        self._locator: Any = None
        self._measure_actors: list[Any] = []
        self._polydata: pv.PolyData | None = None
        self._plotter.set_background(BACKGROUND_BOTTOM, top=BACKGROUND_TOP)
        self._draw_build_volume()

    # ----------------------------------------------------------------- scene

    def _draw_build_volume(self) -> None:
        """Draw the bed and a wireframe of the printable envelope.

        Without these the user has no sense of scale at all, which is the single
        most disorienting thing about a bare 3D view.
        """
        width = self._printer.build_width.millimetres
        depth = self._printer.build_depth.millimetres
        height = self._printer.build_height.millimetres

        plate = pv.Plane(center=(0, 0, 0), direction=(0, 0, 1), i_size=width, j_size=depth)
        self._plotter.add_mesh(
            plate, color=BUILD_PLATE_COLOUR, opacity=0.9, name="build-plate", pickable=False
        )

        envelope = pv.Box(bounds=(-width / 2, width / 2, -depth / 2, depth / 2, 0, height))
        self._plotter.add_mesh(
            envelope,
            color=ENVELOPE_COLOUR,
            style="wireframe",
            line_width=1,
            opacity=0.5,
            name="build-envelope",
            pickable=False,
        )

    def show_mesh(self, mesh: Mesh | None, *, has_problems: bool = False) -> None:
        """Replace whatever model is displayed.

        Args:
            mesh: the geometry to show; ``None`` clears the view.
            has_problems: colour it as a warning rather than as normal geometry.
        """
        self.clear_model()
        if mesh is None or mesh.is_empty:
            return

        self._polydata = to_polydata(mesh)
        self._model_actor = self._plotter.add_mesh(
            self._polydata,
            color=PROBLEM_COLOUR if has_problems else MODEL_COLOUR,
            smooth_shading=True,
            name="model",
            show_edges=False,
        )
        self._show_the_inside(self._model_actor)
        self._locator = None  # invalidated: it belongs to the old geometry

    def clear_model(self) -> None:
        """Remove the model, leaving the build volume in place."""
        self.clear_measurement()
        if self._model_actor is not None:
            self._plotter.remove_actor(self._model_actor, render=False)
        self._model_actor = None
        self._polydata = None
        self._locator = None

    def frame_model(self) -> None:
        """Fit the camera to whatever is on the plate."""
        self._plotter.reset_camera()

    def set_view(self, name: str) -> None:
        """Snap to a named view: ``top``, ``front``, ``right`` or ``iso``."""
        views = {
            "top": self._plotter.view_xy,
            "front": self._plotter.view_xz,
            "right": self._plotter.view_yz,
            "iso": self._plotter.view_isometric,
        }
        action = views.get(name.lower())
        if action is not None:
            action()

    def describe_renderer(self) -> str:
        """Which graphics hardware is drawing, in one line.

        Worth offering because the answer here is surprising and invisible. On
        this machine OpenGL lands on the *integrated* graphics even though a
        discrete card is present, and that cannot be changed from inside the
        application - only in the graphics driver's own control panel. A user
        looking at a slow viewport should be able to read what is drawing it
        rather than guess. See ``docs/research/spike-viewport-gpu.md``.
        """
        window = getattr(self._plotter, "ren_win", None)
        if window is None:
            return NO_RENDERER

        try:
            return renderer_in(str(window.ReportCapabilities()))
        except (AttributeError, RuntimeError, TypeError):
            return NO_RENDERER

    # ---------------------------------------------------------- section view

    def set_section(self, plane: SectionPlane | None) -> None:
        """Cut the view open along a plane, or show the whole model again.

        Clipping is applied to the **mapper**, not by filtering the geometry.
        Nothing is re-meshed, so dragging the plane across a 900,000 triangle
        model costs nothing, and the mesh the rest of the app holds is
        untouched - which matters, because what is exported and what is sliced
        must not depend on how the view happens to be set up.

        The cut is left open rather than capped. A capped section looks more
        like an engineering drawing and hides the very thing somebody cutting a
        part open wants to see: the cavity, and how thick the wall around it
        came out.
        """
        if self._model_actor is None:
            return

        mapper = self._model_actor.GetMapper()
        mapper.RemoveAllClippingPlanes()
        if plane is None:
            return

        import vtk

        cutter = vtk.vtkPlane()
        cutter.SetOrigin(*plane.origin)
        cutter.SetNormal(*plane.normal)
        mapper.AddClippingPlane(cutter)

    def _show_the_inside(self, actor: Any) -> None:
        """Light the inside surfaces, so a cut part reads as one.

        Without this the interior is drawn in the same colour as the outside
        and lit from behind, and a hollow part cut open looks like a solid one
        with a strangely dark face. The back faces are what a section is *for*.
        """
        import vtk

        inside = vtk.vtkProperty()
        inside.SetColor(*_as_fraction(INTERIOR_COLOUR))
        inside.SetSpecular(0.1)
        actor.SetBackfaceProperty(inside)
        actor.GetProperty().SetBackfaceCulling(False)

    def set_wireframe(self, enabled: bool) -> None:
        """Show the model as a wireframe, which makes bad topology visible."""
        if self._model_actor is None:
            return
        self._model_actor.GetProperty().SetRepresentationToWireframe() if enabled else (
            self._model_actor.GetProperty().SetRepresentationToSurface()
        )

    def pick_at(self, x: float, y: float) -> PickResult | None:
        """Cast a ray through a point on screen and return the first hit.

        The half of picking that needs the camera. ``pick`` takes a ray in
        millimetres and is pure geometry; this turns a click into one, using
        VTK's own coordinate transform so the answer agrees with what the user
        can see rather than with a reimplementation of the projection.

        Screen coordinates here are VTK's: origin at the *bottom* left. Qt
        hands out clicks from the top left, so a caller converts.
        """
        renderer = getattr(self._plotter, "renderer", None)
        if renderer is None or self._polydata is None:
            return None

        near = self._world_at(renderer, x, y, 0.0)
        far = self._world_at(renderer, x, y, 1.0)
        if near is None or far is None:
            return None
        return self.pick(near, far)

    @staticmethod
    def _world_at(
        renderer: Any, x: float, y: float, depth: float
    ) -> tuple[float, float, float] | None:
        """One screen point at one depth, in millimetres."""
        try:
            renderer.SetDisplayPoint(x, y, depth)
            renderer.DisplayToWorld()
            wx, wy, wz, w = renderer.GetWorldPoint()
        except (AttributeError, TypeError, ValueError):
            return None
        if not w:
            return None
        return (wx / w, wy / w, wz / w)

    # ------------------------------------------------------------ measuring

    def show_measurement(self, points: Sequence[tuple[float, float, float]]) -> None:
        """Draw the points picked so far, and the line between two of them.

        Drawn rather than described because a measurement whose ends cannot be
        seen is a measurement nobody can check. Removed and redrawn on every
        change: two spheres and a line cost nothing next to the model.
        """
        self.clear_measurement()
        if not points:
            return

        for index, point in enumerate(points):
            self._measure_actors.append(
                self._plotter.add_mesh(
                    pv.Sphere(radius=MEASURE_POINT_MM, center=point),
                    color=MEASURE_COLOUR,
                    name=f"measure-point-{index}",
                    pickable=False,
                    reset_camera=False,
                )
            )

        if len(points) >= 2:
            self._measure_actors.append(
                self._plotter.add_mesh(
                    pv.Line(points[0], points[1]),
                    color=MEASURE_COLOUR,
                    line_width=3,
                    name="measure-line",
                    pickable=False,
                    reset_camera=False,
                )
            )

    def clear_measurement(self) -> None:
        """Take the measurement marks off the model."""
        for actor in self._measure_actors:
            self._plotter.remove_actor(actor, reset_camera=False, render=False)
        self._measure_actors.clear()

    # ---------------------------------------------------------------- picking

    def pick(
        self, start: tuple[float, float, float], end: tuple[float, float, float]
    ) -> PickResult | None:
        """Cast a ray at the model and return the first hit, or ``None``.

        The locator is built once per mesh and reused, which is the difference
        between 4.5 microseconds and 95 milliseconds a pick. Rebuilding it per
        call, as the obvious implementation does, makes selection feel broken.
        """
        if self._polydata is None or self._polydata.n_cells == 0:
            return None

        import vtk  # only needed once something is actually picked

        if self._locator is None:
            self._locator = vtk.vtkCellLocator()
            self._locator.SetDataSet(self._polydata)
            self._locator.BuildLocator()

        t = vtk.mutable(0.0)
        point = [0.0, 0.0, 0.0]
        parametric = [0.0, 0.0, 0.0]
        sub_id = vtk.mutable(0)
        cell_id = vtk.mutable(-1)

        hit = self._locator.IntersectWithLine(
            start, end, 0.0, t, point, parametric, sub_id, cell_id
        )
        if not hit:
            return None
        return (tuple(point), int(cell_id))
