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
from modelpop.presentation.sectioning import Axis, SectionPlane
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

    @pytest.mark.renders
    def test_a_section_actually_changes_the_picture(self, plotter):
        """Clipping that reaches the mapper but not the screen looks identical.

        The API test next door proves the plane is attached; this proves it
        does something, which is a different claim.

        Two traps live in these six lines, and both were paid for here.
        Screenshots are compared against *each other*, not against the
        background, because the background is a gradient and measuring from
        one corner of it counts most of the sky as drawn. And every screenshot
        is preceded by an explicit ``render()``: off-screen, PyVista hands back
        the previous buffer after a clipping change, so without it the picture
        never appears to move and the feature looks broken when it is not.
        """
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(80).dropped_to_bed())
        scene.set_view("front")
        scene.frame_model()

        def shot():
            plotter.render()
            return plotter.screenshot(return_img=True).copy()

        whole = shot()
        scene.set_section(SectionPlane(Axis.X, 0.0))
        halved = shot()
        scene.set_section(None)
        restored = shot()

        def differing(one, two) -> int:
            return int((np.abs(one.astype(int) - two.astype(int)).sum(axis=2) > 20).sum())

        assert differing(whole, halved) > 1_000, "the cut changed nothing on screen"
        assert differing(whole, restored) == 0, "the model did not come back whole"


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


class TestPickingFromTheScreen:
    """Turning a click into a point on the model.

    ``pick`` is pure geometry; this is the half that needs the camera, and the
    half that goes wrong invisibly - an off-by-one in the projection still
    returns *a* point, just not the one under the cursor. So the camera is
    aimed straight down and the centre of the screen is checked against a
    shape whose top is at a known height.
    """

    def looking_down(self, plotter) -> ViewportScene:
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20).dropped_to_bed())
        plotter.view_xy()
        plotter.camera.focal_point = (0.0, 0.0, 10.0)
        plotter.camera.position = (0.0, 0.0, 400.0)
        plotter.camera.parallel_projection = True
        plotter.camera.parallel_scale = 30.0
        plotter.render()
        return scene

    def test_the_middle_of_the_screen_lands_on_the_top_of_the_model(self, plotter):
        scene = self.looking_down(plotter)

        hit = scene.pick_at(320, 240)

        assert hit is not None, "looking straight down at a cube from above"
        assert hit[0][2] == pytest.approx(20.0, abs=0.01)

    def test_a_corner_of_the_screen_misses_a_small_model(self, plotter):
        scene = self.looking_down(plotter)
        assert scene.pick_at(2, 2) is None

    def test_picking_from_the_screen_with_no_model_is_safe(self, plotter):
        assert ViewportScene(plotter).pick_at(320, 240) is None


class TestTheMeasurementOverlay:
    """The marks drawn on the model while it is being measured."""

    def scene_with_a_cube(self, plotter) -> ViewportScene:
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20).dropped_to_bed())
        return scene

    def test_points_are_drawn_where_they_were_picked(self, plotter):
        scene = self.scene_with_a_cube(plotter)
        scene.show_measurement([(0.0, 0.0, 20.0), (10.0, 0.0, 20.0)])

        assert "measure-point-0" in plotter.renderer.actors
        assert "measure-point-1" in plotter.renderer.actors
        assert "measure-line" in plotter.renderer.actors

    def test_one_point_draws_no_line(self, plotter):
        scene = self.scene_with_a_cube(plotter)
        scene.show_measurement([(0.0, 0.0, 20.0)])

        assert "measure-point-0" in plotter.renderer.actors
        assert "measure-line" not in plotter.renderer.actors

    def test_showing_a_new_measurement_replaces_the_old_marks(self, plotter):
        """Otherwise every click leaves a sphere behind for the rest of the session."""
        scene = self.scene_with_a_cube(plotter)
        scene.show_measurement([(0.0, 0.0, 20.0), (10.0, 0.0, 20.0)])
        scene.show_measurement([(0.0, 0.0, 20.0)])

        assert "measure-point-1" not in plotter.renderer.actors
        assert "measure-line" not in plotter.renderer.actors

    def test_clearing_takes_the_marks_off(self, plotter):
        scene = self.scene_with_a_cube(plotter)
        scene.show_measurement([(0.0, 0.0, 20.0), (10.0, 0.0, 20.0)])

        scene.clear_measurement()

        assert "measure-point-0" not in plotter.renderer.actors
        assert "measure-line" not in plotter.renderer.actors

    def test_changing_the_model_takes_the_marks_off_too(self, plotter):
        """A measurement of the old shape, left floating over the new one."""
        scene = self.scene_with_a_cube(plotter)
        scene.show_measurement([(0.0, 0.0, 20.0), (10.0, 0.0, 20.0)])

        scene.clear_model()

        assert "measure-point-0" not in plotter.renderer.actors

    def test_showing_nothing_is_safe(self, plotter):
        self.scene_with_a_cube(plotter).show_measurement([])

    def test_the_marks_are_not_pickable(self, plotter):
        """Measuring off your own measurement mark would be a fine bug."""
        scene = self.scene_with_a_cube(plotter)
        scene.show_measurement([(0.0, 0.0, 20.0), (10.0, 0.0, 20.0)])

        assert not plotter.renderer.actors["measure-point-0"].GetPickable()


class TestSectioning:
    """Cutting the view open, at the level where it becomes actual clipping.

    Off-screen object work, so it belongs in the fast suite: nothing here
    rasterises. What it proves is that the plane the tool produced reaches the
    mapper, and that it comes off again - a clip left behind on a new model is
    a part that looks half-missing for no visible reason.
    """

    def clipping_on(self, plotter) -> int:
        return plotter.renderer.actors["model"].GetMapper().GetNumberOfClippingPlanes()

    def test_a_model_starts_uncut(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        assert self.clipping_on(plotter) == 0

    def test_a_section_clips_the_model(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.set_section(SectionPlane(Axis.X, 0.0))

        assert self.clipping_on(plotter) == 1

    def test_turning_the_section_off_takes_the_clip_away(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.set_section(SectionPlane(Axis.X, 0.0))
        scene.set_section(None)

        assert self.clipping_on(plotter) == 0

    def test_moving_the_section_does_not_stack_up_planes(self, plotter):
        """Dragging the slider calls this on every step."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        for offset in range(-8, 8):
            scene.set_section(SectionPlane(Axis.X, float(offset)))

        assert self.clipping_on(plotter) == 1

    def test_the_plane_reaches_vtk_where_it_was_asked_for(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.set_section(SectionPlane(Axis.Z, 7.5))

        plane = plotter.renderer.actors["model"].GetMapper().GetClippingPlanes().GetItem(0)
        assert plane.GetOrigin() == pytest.approx((0.0, 0.0, 7.5))
        assert plane.GetNormal() == pytest.approx((0.0, 0.0, 1.0))

    def test_flipping_the_section_reverses_the_normal_vtk_is_given(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.set_section(SectionPlane(Axis.Z, 0.0, flipped=True))

        plane = plotter.renderer.actors["model"].GetMapper().GetClippingPlanes().GetItem(0)
        assert plane.GetNormal() == pytest.approx((0.0, 0.0, -1.0))

    def test_sectioning_with_no_model_is_ignored_rather_than_fatal(self, plotter):
        ViewportScene(plotter).set_section(SectionPlane(Axis.X, 0.0))

    def test_the_inside_of_a_cut_model_is_lit(self, plotter):
        """Without this a hollow part cut open reads as a solid one."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        actor = plotter.renderer.actors["model"]

        assert actor.GetBackfaceProperty() is not None
        assert not actor.GetProperty().GetBackfaceCulling()

    def test_a_new_model_comes_in_unclipped(self, plotter):
        """The clip belongs to the old actor; the window reapplies it."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        scene.set_section(SectionPlane(Axis.X, 0.0))
        scene.show_mesh(unit_cube(40))

        assert self.clipping_on(plotter) == 0
