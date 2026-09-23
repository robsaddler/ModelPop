"""The viewport, exercised off-screen.

Runs without a window, so it belongs in the fast suite. What it proves is that
the scene really draws something and that picking is accurate - the two failures
that are invisible until a user complains.

The one test that actually rasterises is marked ``renders``. A GPU-less CI
runner does not fail that test, it dies mid-render with an access violation and
takes the whole suite with it, so CI deselects the marker rather than pretending
to skip it. Everything else here - conversion, camera, picking - is pure VTK
object work and runs anywhere.
"""

import numpy as np
import pytest
import pyvista as pv

from modelpop.domain import Length, Mesh, Unit
from modelpop.domain.printer import PrinterProfile
from modelpop.rendering import ViewportScene, to_polydata

from .strategies import unit_cube


@pytest.fixture
def plotter():
    plot = pv.Plotter(off_screen=True, window_size=(640, 480))
    yield plot
    plot.close()


class TestConversion:
    def test_a_mesh_becomes_drawable_geometry(self):
        poly = to_polydata(unit_cube(10))
        assert poly.n_points == 8
        assert poly.n_cells == 12

    def test_an_empty_mesh_converts_without_exploding(self):
        assert to_polydata(Mesh.empty()).n_cells == 0

    def test_geometry_is_converted_to_millimetres_for_display(self):
        """The plate is drawn in millimetres, so the model must be too.

        A model silently drawn in inches beside a plate drawn in millimetres
        looks like a scale bug and is impossible to diagnose by eye.
        """
        in_inches = unit_cube(1).with_unit(Unit.INCH)
        bounds = to_polydata(in_inches).bounds
        assert bounds[1] - bounds[0] == pytest.approx(25.4)

    def test_the_converted_geometry_matches_the_source(self):
        cube = unit_cube(5)
        poly = to_polydata(cube)
        assert poly.n_cells == cube.triangle_count
        assert poly.volume == pytest.approx(cube.volume, rel=1e-6)


class TestScene:
    def test_the_build_volume_is_drawn_before_any_model(self, plotter):
        ViewportScene(plotter, PrinterProfile.p2s())
        assert "build-plate" in plotter.renderer.actors
        assert "build-envelope" in plotter.renderer.actors

    def test_the_build_volume_matches_the_printer(self, plotter):
        ViewportScene(plotter, PrinterProfile.p2s())
        plate = plotter.renderer.actors["build-plate"]
        xmin, xmax, ymin, ymax, _, _ = plate.GetBounds()
        assert xmax - xmin == pytest.approx(256.0)
        assert ymax - ymin == pytest.approx(256.0)

    def test_showing_a_model_adds_it_to_the_scene(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        assert "model" in plotter.renderer.actors

    def test_showing_a_new_model_replaces_the_old_one(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.show_mesh(unit_cube(40))
        models = [name for name in plotter.renderer.actors if name == "model"]
        assert len(models) == 1

    def test_clearing_removes_the_model_but_keeps_the_plate(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.clear_model()
        assert "model" not in plotter.renderer.actors
        assert "build-plate" in plotter.renderer.actors

    def test_showing_nothing_clears_the_view(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.show_mesh(None)
        assert "model" not in plotter.renderer.actors

    def test_an_empty_mesh_draws_nothing(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(Mesh.empty())
        assert "model" not in plotter.renderer.actors

    @pytest.mark.parametrize("view", ["top", "front", "right", "iso", "ISO"])
    def test_named_views_are_accepted(self, plotter, view):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.set_view(view)  # must not raise

    def test_an_unknown_view_name_is_ignored_rather_than_fatal(self, plotter):
        ViewportScene(plotter).set_view("sideways-ish")


class TestRendering:
    @pytest.mark.renders
    def test_the_scene_actually_draws_pixels(self, plotter):
        """A viewport that silently renders nothing is the worst kind of bug.

        It cost an afternoon during the C# spike: correct layout, no exception,
        and an empty window. Assert on pixels, not on API calls.
        """
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(80).dropped_to_bed())
        scene.frame_model()

        image = plotter.screenshot(return_img=True)
        background = image[0, 0].astype(int)
        drawn = int((np.abs(image.astype(int) - background).sum(axis=2) > 20).sum())
        assert drawn > 5_000, "the viewport rendered nothing recognisable"


class TestPicking:
    def test_a_ray_hits_the_top_of_the_model(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20).dropped_to_bed())

        hit = scene.pick((0, 0, 500), (0, 0, -500))
        assert hit is not None
        point, cell = hit
        assert point[2] == pytest.approx(20.0, abs=1e-6)
        assert cell >= 0

    def test_a_ray_that_misses_returns_nothing(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20).dropped_to_bed())
        assert scene.pick((1000, 1000, 500), (1000, 1000, -500)) is None

    def test_picking_with_no_model_is_safe(self, plotter):
        assert ViewportScene(plotter).pick((0, 0, 10), (0, 0, -10)) is None

    def test_picking_uses_the_new_geometry_after_a_swap(self, plotter):
        """The locator caches the mesh, so a stale one would pick the old shape."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20).dropped_to_bed())
        assert scene.pick((0, 0, 500), (0, 0, -500))[0][2] == pytest.approx(20.0)

        scene.show_mesh(unit_cube(60).dropped_to_bed())
        assert scene.pick((0, 0, 500), (0, 0, -500))[0][2] == pytest.approx(60.0)

    def test_picking_is_accurate_on_a_model_in_inches(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(1).with_unit(Unit.INCH).dropped_to_bed())
        hit = scene.pick((0, 0, 500), (0, 0, -500))
        assert hit is not None
        assert hit[0][2] == pytest.approx(Length.inches(1).millimetres, abs=1e-4)
