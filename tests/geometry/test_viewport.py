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

from typing import Any

import numpy as np
import pytest
import pyvista as pv

from modelpop.domain import Length, Mesh, Unit
from modelpop.domain.printer import PrinterProfile
from modelpop.presentation.sectioning import Axis, SectionPlane
from modelpop.rendering import NO_RENDERER, ViewportScene, renderer_in, to_polydata
from modelpop.rendering.turning import SIDEWAYS, UP_AND_DOWN, Turning

from .strategies import unit_cube


@pytest.fixture
def plotter():
    plot = pv.Plotter(off_screen=True, window_size=(640, 480))
    yield plot
    plot.close()


@pytest.fixture
def plotter_on_screen():
    """A real window, for the few things that need a live interactor.

    Marked ``renders`` wherever it is used, for the same reason as everything
    else that rasterises: a GPU-less runner does not fail, it dies.
    """
    plot = pv.Plotter(window_size=(500, 400))
    plot.show(auto_close=False, interactive=False, interactive_update=True)
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


class TestSayingWhatIsDrawing:
    """Which card the viewport got, reported rather than guessed at.

    On a laptop with switchable graphics OpenGL lands on the integrated chip
    even with a discrete card present, and nothing the application can do
    changes it - see docs/research/spike-viewport-gpu.md. So it says so.

    The reading is a pure function over the driver's own text, which is what
    makes every shape of it - including the empty one an off-screen window
    gives - testable without a graphics context.
    """

    NVIDIA = "\n".join(
        [
            "client glx vendor string:  Mesa",
            "OpenGL vendor string:  NVIDIA Corporation",
            "OpenGL renderer string:  NVIDIA GeForce RTX 4090 Laptop GPU/PCIe",
            "OpenGL version string:  4.6.0",
        ]
    )

    def test_it_reads_the_card_and_who_made_it(self):
        told = renderer_in(self.NVIDIA)
        assert "RTX 4090" in told
        assert "NVIDIA Corporation" in told

    def test_it_is_not_fooled_by_a_similarly_named_line(self):
        """ "client glx vendor string" is not the vendor, and says Mesa here."""
        assert "Mesa" not in renderer_in(self.NVIDIA)

    def test_a_report_with_no_renderer_in_it_says_so(self):
        assert renderer_in("OpenGL version string:  4.6.0") == NO_RENDERER

    def test_an_empty_report_says_so(self):
        """What an off-screen window gives: no device context at all."""
        assert renderer_in("") == NO_RENDERER

    def test_a_renderer_with_no_vendor_beside_it_is_still_reported(self):
        told = renderer_in("OpenGL renderer string:  llvmpipe")
        assert "llvmpipe" in told

    def test_an_off_screen_scene_answers_without_raising(self, plotter):
        """The settings panel shows this, and must open either way."""
        told = ViewportScene(plotter).describe_renderer()
        assert told == NO_RENDERER or told.startswith("Drawing on")


class TestDragHandles:
    """Handles on the model, and what happens to the actor underneath them.

    The attaching needs a live interactor, which an off-screen plotter does not
    have - so that half is marked ``renders``. The half that matters most is
    not: the actor's own transform has to be cleared once a drag has been
    turned into commands, because the rebuilt model already stands where it was
    dragged to and leaving both on moves the part twice as far as it was
    dragged.
    """

    def test_the_handles_go_on_a_model_and_say_that_they_did(self, plotter):
        """The answer is what the menu item needs: a toggle that silently does
        nothing is worse than one that is greyed out."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))

        assert scene.start_dragging(lambda _: None) is True
        assert scene.is_dragging

    @pytest.mark.renders
    def test_it_reports_the_offset_while_the_drag_is_still_happening(self, plotter_on_screen):
        """Silence until the mouse comes up is what made this feel broken.

        A drag that missed the arrow and a drag that is working look identical
        for as long as the button is held, unless something says so.
        """
        scene = ViewportScene(plotter_on_screen)
        scene.show_mesh(unit_cube(20))
        told: list[object] = []

        assert scene.start_dragging(lambda _: None, told.append)
        assert scene._drag_widget._on_move == told.append

    def test_a_drag_without_live_reporting_is_still_allowed(self, plotter):
        """The callback is optional, so nothing else has to pass one."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        assert scene.start_dragging(lambda _: None) is True

    def test_there_is_nothing_to_drag_before_a_model_is_open(self, plotter):
        assert ViewportScene(plotter).start_dragging(lambda _: None) is False

    def test_stopping_when_nothing_was_started_is_safe(self, plotter):
        ViewportScene(plotter).stop_dragging()

    def test_forgetting_a_drag_clears_the_actor_transform(self, plotter):
        """The bug this prevents: the part jumps twice as far as it was dragged."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(20))
        actor = plotter.renderer.actors["model"]

        dragged = np.eye(4)
        dragged[0:3, 3] = (15.0, 0.0, 0.0)
        actor.user_matrix = dragged
        scene.forget_drag()

        assert np.allclose(np.asarray(actor.user_matrix), np.eye(4))

    def test_forgetting_a_drag_with_no_model_is_safe(self, plotter):
        ViewportScene(plotter).forget_drag()

    @pytest.mark.renders
    def test_the_handles_reach_outside_the_model(self, plotter_on_screen):
        """A handle buried inside the shape is one nobody can click.

        PyVista's default scale of 0.15 put every arrow *within* the model -
        measured: the +Z arrow centred at z=6 on a sphere spanning -20 to +20.
        They rendered, they highlighted on hover, and a click aimed at one
        landed on the model in front of it. The report was "nothing happens".
        """
        scene = ViewportScene(plotter_on_screen)
        cube = unit_cube(40).dropped_to_bed()
        scene.show_mesh(cube)
        assert scene.start_dragging(lambda _: None)

        widest = max(cube.bounds.width.millimetres, cube.bounds.depth.millimetres) / 2
        for arrow in scene._drag_widget._arrows:
            reach = max(abs(value) for value in arrow.GetBounds())
            assert reach > widest, (
                f"a handle reaching {reach:.1f} mm is inside a model "
                f"{widest:.1f} mm from centre - it cannot be clicked"
            )

    @pytest.mark.renders
    def test_the_handles_stay_on_across_a_new_model(self, plotter_on_screen):
        """A rebuild replaces the actor the handles are bolted to.

        They used to be removed and re-added around that swap, which meant
        they blinked out for the length of a rebuild - seconds of subprocess -
        and it was reported as the controls disappearing. They are re-pointed
        at the new actor instead, and stay in the scene from being switched on
        to being switched off.
        """
        scene = ViewportScene(plotter_on_screen)
        scene.show_mesh(unit_cube(40).dropped_to_bed())

        assert scene.start_dragging(lambda _: None)
        assert scene.is_dragging
        handles = scene._drag_widget

        scene.show_mesh(unit_cube(20).dropped_to_bed())

        assert scene.is_dragging, "the handles came off when the model was replaced"
        assert scene._drag_widget is handles, "they were rebuilt rather than re-pointed"
        assert handles._actor is scene._model_actor, (
            "they are still bolted to the actor that went away"
        )

        scene.stop_dragging()
        assert not scene.is_dragging

    @pytest.mark.renders
    def test_they_follow_a_part_that_has_changed_size(self, plotter_on_screen):
        """Re-pointing is not enough on its own: they have to fit the new part."""
        scene = ViewportScene(plotter_on_screen)
        scene.show_mesh(unit_cube(40).dropped_to_bed())
        scene.start_dragging(lambda _: None)
        small = max(abs(v) for v in scene._drag_widget._arrows[2].GetBounds())

        scene.show_mesh(unit_cube(120).dropped_to_bed())
        large = max(abs(v) for v in scene._drag_widget._arrows[2].GetBounds())

        assert large > small * 2, "the handles kept the old part's size"

    def test_pausing_leaves_them_on_screen_but_ungrabbable(self, plotter):
        """What happens while a released drag is being turned into features."""
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(40).dropped_to_bed())
        scene.start_dragging(lambda _: None)

        scene.pause_dragging()

        assert scene.is_dragging, "the handles left the scene"
        assert not scene.can_be_dragged, "they would still accept a grab"

    def test_a_new_model_wakes_them_up_again(self, plotter):
        scene = ViewportScene(plotter)
        scene.show_mesh(unit_cube(40).dropped_to_bed())
        scene.start_dragging(lambda _: None)
        scene.pause_dragging()

        scene.show_mesh(unit_cube(40).dropped_to_bed())

        assert scene.can_be_dragged


class TestNamingThePrinter:
    """The wireframe box has to say whose build volume it is.

    Without the label the box reads as scenery. The user asked the question
    directly - "what is the empty cube I start with?" - and then, separately,
    a sphere appearing half through the plate looked like a bug rather than a
    shape that had not been put on the bed yet. Both are the same missing
    sentence.
    """

    # VTK numbers the corners of a CornerAnnotation, and upper-left is 2.
    # Reading the text out of that slot is the positional assertion: if the
    # label moved to another corner, its own corner would come back empty.
    UPPER_LEFT = 2

    def label_in(self, plotter) -> Any:
        return plotter.renderer.actors.get("printer-label")

    def text_in(self, plotter) -> str:
        actor = self.label_in(plotter)
        assert actor is not None, "there is no printer label in the scene at all"
        return actor.GetText(self.UPPER_LEFT) or ""

    def test_the_scene_names_the_printer(self, plotter):
        ViewportScene(plotter, PrinterProfile.p2s())
        assert "Bambu Lab P2S" in self.text_in(plotter)

    def test_it_says_what_the_box_measures(self, plotter):
        """So the box is readable as a size, not just as a name."""
        ViewportScene(plotter, PrinterProfile.p2s())
        told = self.text_in(plotter)
        assert told.count("256") == 3
        assert "build volume" in told

    def test_it_names_whichever_printer_it_was_given(self, plotter):
        """Not a hard-coded string. A different profile must say so."""
        other = PrinterProfile(
            model="Something Else X1",
            build_width=Length.mm(180),
            build_depth=Length.mm(180),
            build_height=Length.mm(180),
        )
        ViewportScene(plotter, other)
        told = self.text_in(plotter)
        assert "Something Else X1" in told
        assert "Bambu" not in told
        assert "180" in told

    def test_it_is_in_the_top_left_and_nowhere_else(self, plotter):
        """Where it was asked for - the top right was the first attempt."""
        ViewportScene(plotter, PrinterProfile.p2s())
        actor = self.label_in(plotter)
        # An unused corner comes back as None rather than an empty string.
        corners = {index: actor.GetText(index) or "" for index in range(4)}

        assert "Bambu Lab P2S" in corners[self.UPPER_LEFT]
        occupied = [index for index, text in corners.items() if text.strip()]
        assert occupied == [self.UPPER_LEFT], (
            f"the label is drawn in corners {occupied}, not the top left alone"
        )

    def test_it_survives_a_model_arriving_and_leaving(self, plotter):
        """Showing and clearing a model must not take the label with it."""
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_mesh(unit_cube(40))
        assert "Bambu Lab P2S" in self.text_in(plotter)
        scene.clear_model()
        assert "Bambu Lab P2S" in self.text_in(plotter)


class TestTheProjection:
    """No perspective. The scene is mostly a 256 mm box and it must look like one.

    VTK's default camera is perspective with a 30-degree view angle. Zoomed in
    on a build volume that is actively misleading: the envelope's edges fan
    out, the far wall shrinks, and the box stops reading as a box. It was
    reported twice - "the default cube distorts into not a cube" and "the
    printer perspective went wonky" - and neither was the geometry.
    """

    def test_the_camera_draws_without_perspective(self, plotter):
        ViewportScene(plotter)
        assert plotter.camera.parallel_projection

    def test_the_envelope_stays_a_box_however_close_the_camera_gets(self, plotter):
        """The property, rather than a screenshot: parallel edges stay parallel.

        The top and bottom edges of the envelope's front face are the same
        length in world space. Under perspective their projected lengths
        diverge as the camera closes in; under parallel projection they cannot.
        """
        import vtk

        scene = ViewportScene(plotter)
        scene.set_view("iso")
        plotter.reset_camera()

        width = scene._printer.build_width.millimetres
        height = scene._printer.build_height.millimetres

        def on_screen(point):
            coordinate = vtk.vtkCoordinate()
            coordinate.SetCoordinateSystemToWorld()
            coordinate.SetValue(*point)
            return np.array(coordinate.GetComputedDoubleDisplayValue(plotter.renderer))

        def front_edges():
            bottom = np.linalg.norm(
                on_screen((-width / 2, -width / 2, 0.0)) - on_screen((width / 2, -width / 2, 0.0))
            )
            top = np.linalg.norm(
                on_screen((-width / 2, -width / 2, height))
                - on_screen((width / 2, -width / 2, height))
            )
            return bottom, top

        for zoom in (1.0, 2.0, 4.0):
            plotter.camera.zoom(zoom)
            plotter.render()
            bottom, top = front_edges()
            assert top == pytest.approx(bottom, rel=1e-3), (
                f"at zoom {zoom} the top edge measured {top:.1f}px and the bottom "
                f"{bottom:.1f}px - the box is being drawn with perspective"
            )

    def test_two_parts_of_the_same_size_measure_the_same_wherever_they_sit(self, plotter):
        """What parallel projection is actually for: a view you can judge size in."""
        import vtk

        scene = ViewportScene(plotter)
        scene.set_view("iso")
        plotter.reset_camera()

        def span(centre):
            coordinate = vtk.vtkCoordinate()
            coordinate.SetCoordinateSystemToWorld()
            points = []
            for offset in ((-10.0, 0.0, 0.0), (10.0, 0.0, 0.0)):
                coordinate.SetValue(*(np.array(centre) + np.array(offset)))
                points.append(np.array(coordinate.GetComputedDoubleDisplayValue(plotter.renderer)))
            return float(np.linalg.norm(points[0] - points[1]))

        near_the_front = span((0.0, -100.0, 10.0))
        near_the_back = span((0.0, 100.0, 10.0))
        assert near_the_back == pytest.approx(near_the_front, rel=1e-3)


class TestLookingIntoThePrinter:
    """One command that puts the view back where somebody can work.

    The named views only turn the camera. After panning and zooming about,
    "front" still leaves the scene off to one side at whatever magnification it
    had - which is no use as a way of getting un-lost, and getting un-lost is
    what it is wanted for.
    """

    def lost(self, plotter) -> ViewportScene:
        """A scene whose camera has been thoroughly misplaced."""
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_mesh(unit_cube(40).dropped_to_bed())
        plotter.camera.position = (900.0, -1200.0, 700.0)
        plotter.camera.focal_point = (400.0, 400.0, -200.0)
        plotter.camera.zoom(6.0)
        return scene

    def test_it_stands_in_front_of_the_machine(self, plotter):
        scene = self.lost(plotter)
        scene.look_into_the_printer()

        looking = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        assert looking[1] < 0, "the camera is not in front of the printer"
        assert np.asarray(plotter.camera.up) == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)

    def test_it_is_tilted_enough_to_see_the_plate(self, plotter):
        """Dead square on puts the build plate exactly edge-on.

        The one surface everything stands on becomes an invisible line, and
        there is no sense of depth at all. A few degrees is all it takes.
        """
        scene = self.lost(plotter)
        scene.look_into_the_printer()

        looking = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        looking = looking / np.linalg.norm(looking)
        above = np.degrees(np.arcsin(looking[2]))

        assert above > 5.0, f"only {above:.0f} degrees up - the plate is still edge-on"
        assert above < 40.0, f"{above:.0f} degrees up is a bird's eye view, not looking in"

    def test_it_steps_a_little_to_the_left(self, plotter):
        """So the volume has depth rather than reading as a flat rectangle."""
        scene = self.lost(plotter)
        scene.look_into_the_printer()

        looking = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        flat = looking[:2] / np.linalg.norm(looking[:2])
        aside = np.degrees(np.arcsin(abs(flat[0])))

        assert looking[0] < 0, "it stepped to the right, not the left"
        assert aside > 5.0, f"only {aside:.0f} degrees round - still square on"
        assert aside < 40.0, f"{aside:.0f} degrees round is a corner view, not a front one"

    def test_a_named_view_is_still_exactly_that_view(self, plotter):
        """The tilt is for the resting view, not for anything asked for by name."""
        scene = self.lost(plotter)
        scene.look_into_the_printer("front")

        looking = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        assert looking[0] == pytest.approx(0.0, abs=1e-6)
        assert looking[2] == pytest.approx(0.0, abs=1e-6)

    def test_it_centres_on_the_build_volume(self, plotter):
        scene = self.lost(plotter)
        scene.look_into_the_printer()

        height = scene._printer.build_height.millimetres
        assert np.asarray(plotter.camera.focal_point) == pytest.approx(
            [0.0, 0.0, height / 2], abs=0.5
        )

    def test_the_whole_build_volume_is_in_frame(self, plotter):
        """And with a little air, not flush against the window edge."""
        scene = self.lost(plotter)
        scene.look_into_the_printer()

        half_height = scene._printer.build_height.millimetres / 2
        scale = plotter.camera.parallel_scale
        assert scale >= half_height, f"the volume is taller than the view ({scale:.0f})"
        assert scale < half_height * 1.6, f"it is framed far looser than asked ({scale:.0f})"

    def test_it_fills_the_frame_rather_than_fitting_a_sphere(self, plotter):
        """VTK's own reset fits the bounding sphere, which is far too loose.

        A 256 mm cube seen square on has a 443 mm diagonal, so fitting the
        sphere leaves it filling barely half the window.
        """
        scene = self.lost(plotter)
        scene.look_into_the_printer()
        fitted = plotter.camera.parallel_scale

        plotter.reset_camera(
            bounds=(-128.0, 128.0, -128.0, 128.0, 0.0, 256.0),
        )
        sphere = plotter.camera.parallel_scale

        assert fitted < sphere * 0.9, (
            f"framed at {fitted:.0f}, barely tighter than the sphere fit {sphere:.0f}"
        )

    def test_a_part_parked_outside_the_volume_does_not_drag_the_view_out(self, plotter):
        """Where the build volume is is exactly what this is for."""
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_mesh(unit_cube(20).dropped_to_bed())
        scene.look_into_the_printer()
        framed = plotter.camera.parallel_scale

        far_away = unit_cube(20)
        scene.show_mesh(Mesh(far_away.vertices + np.array([2000.0, 0.0, 0.0]), far_away.faces))
        scene.look_into_the_printer()

        assert plotter.camera.parallel_scale == pytest.approx(framed)

    def test_it_can_be_asked_for_another_angle(self, plotter):
        scene = self.lost(plotter)
        scene.look_into_the_printer("top")

        looking = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        assert looking[2] > 0
        assert looking[0] == pytest.approx(0.0, abs=1e-6)


class TestTheAxisMarkers:
    """X, Y and Z on the plate, because nobody remembers which is which.

    In the same red, green and blue the drag arrows use, so the arrow being
    pulled and the axis it runs along are obviously the same thing.
    """

    def test_they_are_on_by_default(self, plotter):
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        assert scene.axes_are_shown

    def test_all_three_are_drawn(self, plotter):
        ViewportScene(plotter, PrinterProfile.p2s())
        drawn = {name for name in plotter.renderer.actors if name.startswith("axis-")}

        assert {"axis-X", "axis-Y", "axis-Z"} <= drawn

    def test_they_can_be_taken_away(self, plotter):
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_axes(False)

        assert not scene.axes_are_shown
        assert [n for n in plotter.renderer.actors if n.startswith("axis-")] == []

    def test_they_can_be_put_back(self, plotter):
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_axes(False)
        scene.show_axes(True)

        assert scene.axes_are_shown
        assert [n for n in plotter.renderer.actors if n.startswith("axis-")]

    def test_switching_them_on_twice_does_not_double_them(self, plotter):
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_axes(True)
        scene.show_axes(True)

        arrows = [n for n in plotter.renderer.actors if n.startswith("axis-") and "label" not in n]
        assert len(arrows) == 3

    def test_they_sit_at_a_corner_rather_than_in_the_middle(self, plotter):
        """The middle of the plate is where the model stands."""
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        half = scene._printer.build_width.millimetres / 2

        marker = plotter.renderer.actors["axis-X"]
        assert marker.GetBounds()[0] == pytest.approx(-half, abs=1.0)

    def test_they_wear_the_same_colours_as_the_drag_arrows(self, plotter):
        from modelpop.rendering.drag_handles import AXIS_COLOURS

        ViewportScene(plotter, PrinterProfile.p2s())
        for index, label in enumerate("XYZ"):
            drawn = plotter.renderer.actors[f"axis-{label}"].prop.color.hex_rgb
            assert drawn.lower() == AXIS_COLOURS[index].lower()

    def test_they_do_not_change_what_fitting_the_view_means(self, plotter):
        """A marker is furniture, not content.

        Counted in the scene's bounds it widens what "fit to the model" means
        and quietly moves everything on screen - which showed up as a pick
        through the middle of the window landing on the plate instead of the
        part.
        """
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_mesh(unit_cube(40).dropped_to_bed())

        scene.show_axes(False)
        plotter.reset_camera()
        without = plotter.camera.parallel_scale

        scene.show_axes(True)
        plotter.reset_camera()

        assert plotter.camera.parallel_scale == pytest.approx(without)


class TestTurningTheView:
    """The view turns like a turntable, and the horizon never rolls.

    A trackball - what this did, and what VTK does by default - adds a third
    freedom nobody asked for, and it is the one that arrives as "I tried to
    turn it left and now it is skewed".
    """

    def scene(self, plotter) -> ViewportScene:
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.look_into_the_printer()
        return scene

    def spin(self, plotter) -> float:
        away = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        return float(np.degrees(np.arctan2(away[0], -away[1])))

    def height(self, plotter) -> float:
        away = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        return float(np.degrees(np.arcsin(away[2] / np.linalg.norm(away))))

    def roll(self, plotter) -> float:
        """How far the horizon has tipped. The thing being designed out."""
        camera = plotter.camera
        forward = np.array(camera.focal_point) - np.array(camera.position)
        forward = forward / np.linalg.norm(forward)
        right = np.cross(forward, [0.0, 0.0, 1.0])
        right = right / np.linalg.norm(right)
        up = np.array(camera.up) / np.linalg.norm(camera.up)
        return float(np.degrees(np.arcsin(np.clip(np.dot(up, right), -1.0, 1.0))))

    def test_nothing_is_held_to_begin_with(self, plotter):
        assert ViewportScene(plotter, PrinterProfile.p2s()).turning_held == frozenset()

    def test_dragging_sideways_spins_it_round(self, plotter):
        scene = self.scene(plotter)
        before = self.spin(plotter)
        scene.drag_the_view_by(120, 0)

        assert abs(self.spin(plotter) - before) > 10.0

    def test_dragging_up_raises_the_eye(self, plotter):
        scene = self.scene(plotter)
        before = self.height(plotter)
        scene.drag_the_view_by(0, 60)

        assert self.height(plotter) > before

    def test_the_horizon_never_rolls(self, plotter):
        """However hard it is dragged. This is the whole complaint."""
        scene = self.scene(plotter)
        for _ in range(15):
            scene.drag_the_view_by(80, 40)

        assert self.roll(plotter) == pytest.approx(0.0, abs=1e-3)

    def test_it_never_goes_over_the_top(self, plotter):
        """Upside down, with the mouse working backwards, reads as broken."""
        scene = self.scene(plotter)
        for _ in range(40):
            scene.drag_the_view_by(0, 60)

        assert abs(self.height(plotter)) < 90.0

    def test_it_never_goes_under_the_bottom(self, plotter):
        scene = self.scene(plotter)
        for _ in range(40):
            scene.drag_the_view_by(0, -60)

        assert abs(self.height(plotter)) < 90.0


class TestHoldingADragDirection:
    """Either direction can be held, named after the hand and not the axis.

    A first attempt offered X, Y and Z, and five people testing it could not
    map those onto what their hand was doing: "rotate about Z" and "drag left
    and right" are the same thing, and nobody should have to translate between
    them to look at their model.
    """

    def scene(self, plotter) -> ViewportScene:
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.look_into_the_printer()
        return scene

    def spin(self, plotter) -> float:
        away = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        return float(np.degrees(np.arctan2(away[0], -away[1])))

    def height(self, plotter) -> float:
        away = np.array(plotter.camera.position) - np.array(plotter.camera.focal_point)
        return float(np.degrees(np.arcsin(away[2] / np.linalg.norm(away))))

    def test_holding_left_and_right_stops_it_spinning(self, plotter):
        scene = self.scene(plotter)
        scene.hold_turning(SIDEWAYS, True)
        before = self.spin(plotter)

        scene.drag_the_view_by(120, 60)
        assert self.spin(plotter) == pytest.approx(before, abs=1e-6)

    def test_holding_left_and_right_still_lets_it_tip(self, plotter):
        scene = self.scene(plotter)
        scene.hold_turning(SIDEWAYS, True)
        before = self.height(plotter)

        scene.drag_the_view_by(120, 60)
        assert self.height(plotter) != pytest.approx(before)

    def test_holding_up_and_down_stops_it_tipping(self, plotter):
        scene = self.scene(plotter)
        scene.hold_turning(UP_AND_DOWN, True)
        before = self.height(plotter)

        scene.drag_the_view_by(120, 60)
        assert self.height(plotter) == pytest.approx(before, abs=1e-6)

    def test_holding_up_and_down_still_lets_it_spin(self, plotter):
        scene = self.scene(plotter)
        scene.hold_turning(UP_AND_DOWN, True)
        before = self.spin(plotter)

        scene.drag_the_view_by(120, 60)
        assert abs(self.spin(plotter) - before) > 10.0

    def test_holding_both_stops_it_turning(self, plotter):
        scene = self.scene(plotter)
        scene.hold_turning(SIDEWAYS, True)
        scene.hold_turning(UP_AND_DOWN, True)
        before = np.array(plotter.camera.position)

        scene.drag_the_view_by(120, 90)
        assert np.asarray(plotter.camera.position) == pytest.approx(before)

    def test_a_hold_can_be_let_go(self, plotter):
        scene = self.scene(plotter)
        scene.hold_turning(SIDEWAYS, True)
        scene.hold_turning(SIDEWAYS, False)
        before = self.spin(plotter)

        scene.drag_the_view_by(120, 0)
        assert abs(self.spin(plotter) - before) > 10.0

    def test_a_direction_it_does_not_know_is_ignored(self, plotter):
        scene = self.scene(plotter)
        scene.hold_turning("diagonally", True)
        assert scene.turning_held == frozenset()


class TestSlidingTheViewInstead:
    """Shift is left to the camera controls, so a drag can move rather than turn.

    "How do I not rotate the viewport but move the camera up/down on the Z
    axis? When I zoom in, I can't then drag down from the head to see the feet.
    Any drag rotates everything?"

    It did. Turning claimed every left-button press, including the one the
    interactor style would have panned with, so there was no way to travel
    along a tall model without spinning it round first. The press now looks at
    Shift and stands aside.

    Driven at ``Turning`` directly rather than through real mouse events: what
    is being asserted is that the press is *not* taken, and an event nobody
    claims is exactly what cannot be seen from the outside.
    """

    def turning(self, plotter) -> Turning:
        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.look_into_the_printer()
        return scene._turning

    def test_a_plain_press_is_taken(self, plotter):
        turning = self.turning(plotter)
        turning._pressed(_Pressing(shift=False), "LeftButtonPressEvent")

        assert turning._turning, "a plain drag no longer turns the view"

    def test_a_press_with_shift_is_left_alone(self, plotter):
        turning = self.turning(plotter)
        turning._pressed(_Pressing(shift=True), "LeftButtonPressEvent")

        assert not turning._turning, "Shift+drag was claimed as a turn, so the view cannot be slid"

    def test_a_shift_drag_moves_nothing_by_itself(self, plotter):
        """The camera controls underneath do the sliding, not this."""
        turning = self.turning(plotter)
        before = tuple(plotter.camera.position)

        turning._pressed(_Pressing(shift=True), "LeftButtonPressEvent")
        turning._moved(_Pressing(shift=True, at=(100, 300)), "MouseMoveEvent")

        assert tuple(plotter.camera.position) == before

    def test_letting_go_of_a_shift_drag_is_left_alone_too(self, plotter):
        """Claiming the release would leave the camera controls mid-drag."""
        turning = self.turning(plotter)
        turning._pressed(_Pressing(shift=True), "LeftButtonPressEvent")
        release = _Pressing(shift=True)
        turning._released(release, "LeftButtonReleaseEvent")

        assert release.claimed == 0, "the release was taken from the camera controls"


class _Pressing:
    """Just enough of an interactor to press a button at a point."""

    def __init__(self, shift: bool = False, at: tuple[int, int] = (200, 200)) -> None:
        self._shift = shift
        self._at = at
        self.claimed = 0

    def GetShiftKey(self) -> int:  # noqa: N802 - VTK's spelling
        return 1 if self._shift else 0

    def GetEventPosition(self) -> tuple[int, int]:  # noqa: N802
        return self._at

    def GetCommand(self, _tag):  # noqa: N802
        self.claimed += 1
        return


class TestItOpensFramedOnThePrinter:
    """A new window must show the printer, not the inside of the plate.

    "When app starts, we're zoomed right into plate. Should start using the
    Look into Printer view."

    VTK's default camera sits two millimetres from the origin with a parallel
    scale of 1. A scene that is never told otherwise opens about a hundred and
    eighty times too far in, staring at the middle of a build plate with
    nothing on screen to explain why - measured at scale 1 against the 181 the
    framed view uses.
    """

    def test_a_new_scene_is_already_framed(self, plotter):
        scene = ViewportScene(plotter, PrinterProfile.p2s())

        assert plotter.camera.parallel_scale > 100.0, (
            f"it opened at a parallel scale of {plotter.camera.parallel_scale:.0f}"
        )
        assert scene is not None

    def test_it_opens_where_looking_in_would_put_it(self, plotter):
        """Not merely somewhere sensible - the same place as the Home view."""
        ViewportScene(plotter, PrinterProfile.p2s())
        opened = (
            tuple(plotter.camera.position),
            tuple(plotter.camera.focal_point),
            plotter.camera.parallel_scale,
        )

        ViewportScene(plotter, PrinterProfile.p2s()).look_into_the_printer()
        homed = (
            tuple(plotter.camera.position),
            tuple(plotter.camera.focal_point),
            plotter.camera.parallel_scale,
        )

        assert opened[1] == pytest.approx(homed[1], abs=1e-6)
        assert opened[2] == pytest.approx(homed[2], rel=1e-6)
        assert opened[0] == pytest.approx(homed[0], rel=1e-6)

    def test_it_looks_at_the_middle_of_the_build_volume(self, plotter):
        ViewportScene(plotter, PrinterProfile.p2s())
        focus = plotter.camera.focal_point

        assert focus[0] == pytest.approx(0.0, abs=1e-6)
        assert focus[1] == pytest.approx(0.0, abs=1e-6)
        assert focus[2] == pytest.approx(128.0, abs=1.0), "it is not looking at the middle"

    def test_a_smaller_printer_is_framed_more_closely(self, plotter):
        """The framing follows the bed, so an A1 mini does not open tiny."""
        from modelpop.domain.units import Length

        mini = PrinterProfile(
            model="Bambu Lab A1 mini",
            build_width=Length.mm(180),
            build_depth=Length.mm(180),
            build_height=Length.mm(180),
        )
        ViewportScene(plotter, mini)
        small = plotter.camera.parallel_scale

        ViewportScene(plotter, PrinterProfile.p2s())
        large = plotter.camera.parallel_scale

        assert small < large, f"the mini framed at {small:.0f}, the P2S at {large:.0f}"
