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
    def test_the_handles_attach_and_survive_a_new_model(self, plotter_on_screen):
        """A rebuild replaces the actor the handles are bolted to."""
        scene = ViewportScene(plotter_on_screen)
        scene.show_mesh(unit_cube(40).dropped_to_bed())

        assert scene.start_dragging(lambda _: None)
        assert scene.is_dragging

        scene.show_mesh(unit_cube(20).dropped_to_bed())
        assert not scene.is_dragging, "the handles stayed on the actor that went away"
        assert scene.start_dragging(lambda _: None), "and cannot go back on the new one"

        scene.stop_dragging()
        assert not scene.is_dragging


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
