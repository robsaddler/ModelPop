"""The drag handles, and the arithmetic that makes them track the cursor.

The maths is pure and lives in two functions, so the part that actually broke
before is testable without a graphics context at all. What broke was not the
plumbing - PyVista's widget picked, highlighted, pressed and released perfectly
well. What broke was that its translation was, in its own words, "not
physically accurate" and "ignores zoom": it multiplied a world coordinate by
the actor's length times two, so a part moved by an amount that depended on how
big it was and not on where the camera stood. Zoomed in, the mouse had to cross
the desk twice.

So the property that matters is stated here directly: whatever the camera is
doing, the point under the cursor at the start of a drag is the point under the
cursor at the end of it.
"""

from typing import NamedTuple

import numpy as np
import pytest

from modelpop.rendering.drag_handles import (
    AXIS_COLOURS,
    RING_LEAST,
    RING_MOST,
    DragHandles,
    along_axis,
    on_plane,
)


@pytest.fixture
def plotter():
    """Off-screen, so everything but the observers can be exercised.

    Attaching observers needs a live interactor, which an off-screen plotter
    has not got - so ``DragHandles`` builds and can be driven here, and the
    handful of tests that need real mouse events are marked ``renders``.
    """
    import pyvista as pv

    plot = pv.Plotter(off_screen=True, window_size=(640, 480))
    yield plot
    plot.close()


# How close to the edge of the window a synthetic drag may go.
EDGE_MARGIN = 30

X = np.array([1.0, 0.0, 0.0])
Y = np.array([0.0, 1.0, 0.0])
Z = np.array([0.0, 0.0, 1.0])
ORIGIN = np.zeros(3)


def unit(vector) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float64)
    return array / np.linalg.norm(array)


class TestReadingAlongAnAxis:
    """Where the cursor's ray comes closest to the axis being dragged."""

    def test_a_ray_straight_at_a_point_on_the_axis_reads_that_point(self):
        """The simplest case, and the one every other answer is relative to."""
        found = along_axis(ORIGIN, Z, ray_from=np.array([50.0, 0.0, 12.0]), ray_along=-X)
        assert found == pytest.approx(12.0)

    def test_it_reads_negative_below_the_anchor(self):
        found = along_axis(ORIGIN, Z, ray_from=np.array([50.0, 0.0, -7.5]), ray_along=-X)
        assert found == pytest.approx(-7.5)

    def test_the_anchor_moves_the_zero(self):
        """The reading is absolute, so a drag is the difference of two."""
        anchor = np.array([0.0, 0.0, 100.0])
        found = along_axis(anchor, Z, ray_from=np.array([50.0, 0.0, 112.0]), ray_along=-X)
        assert found == pytest.approx(12.0)

    def test_a_skew_ray_still_resolves_to_the_closest_approach(self):
        """The general case: the ray never touches the axis at all."""
        found = along_axis(
            ORIGIN, Z, ray_from=np.array([40.0, 40.0, 9.0]), ray_along=unit([-1.0, -1.0, 0.0])
        )
        assert found == pytest.approx(9.0)

    def test_it_refuses_when_the_camera_looks_down_the_axis(self):
        """Unbounded, not merely imprecise: a pixel would mean metres.

        Refusing leaves the part still until the view is turned, which is far
        better than flinging it out of the build volume.
        """
        assert along_axis(ORIGIN, Z, ray_from=np.array([0.0, 0.0, 90.0]), ray_along=-Z) is None

    def test_it_refuses_just_off_parallel_too(self):
        nearly = unit([0.0, 0.0002, -1.0])
        assert along_axis(ORIGIN, Z, ray_from=np.array([0.0, 0.0, 90.0]), ray_along=nearly) is None

    def test_the_answer_does_not_depend_on_how_far_away_the_camera_is(self):
        """The defect this replaces, stated as a test.

        The same cursor ray from twice the distance must read the same
        millimetre. PyVista's version scaled by the actor's size instead, so
        this was exactly what it got wrong.
        """
        near = along_axis(ORIGIN, Z, ray_from=np.array([30.0, 0.0, 8.0]), ray_along=-X)
        far = along_axis(ORIGIN, Z, ray_from=np.array([3000.0, 0.0, 8.0]), ray_along=-X)
        assert near == pytest.approx(far)

    def test_the_answer_does_not_depend_on_the_size_of_the_part(self):
        """Nothing here knows the part's dimensions, and that is the point."""
        reading = along_axis(ORIGIN, Z, ray_from=np.array([10.0, 0.0, 4.0]), ray_along=-X)
        assert reading == pytest.approx(4.0)

    @pytest.mark.parametrize("axis", [X, Y, Z])
    def test_every_axis_behaves_the_same(self, axis):
        offset = axis * 6.0
        sideways = unit(np.roll(axis, 1))
        found = along_axis(ORIGIN, axis, ray_from=offset + sideways * 25.0, ray_along=-sideways)
        assert found == pytest.approx(6.0)


class TestReadingRoundARing:
    def test_a_ray_through_the_plane_lands_on_it(self):
        hit = on_plane(ORIGIN, Z, ray_from=np.array([5.0, 3.0, 40.0]), ray_along=-Z)
        assert hit == pytest.approx([5.0, 3.0, 0.0])

    def test_the_plane_follows_its_centre(self):
        hit = on_plane(np.array([0.0, 0.0, 15.0]), Z, np.array([1.0, 2.0, 40.0]), -Z)
        assert hit == pytest.approx([1.0, 2.0, 15.0])

    def test_a_ray_along_the_plane_has_no_crossing(self):
        """The ring seen exactly edge-on: there is no angle to read."""
        assert on_plane(ORIGIN, Z, ray_from=np.array([0.0, 0.0, 0.0]), ray_along=X) is None

    def test_it_works_from_behind_the_plane_as_well(self):
        hit = on_plane(ORIGIN, Z, ray_from=np.array([2.0, 2.0, -40.0]), ray_along=Z)
        assert hit == pytest.approx([2.0, 2.0, 0.0])


class TestTheHandlesThemselves:
    """Built off-screen, where there is no interactor to observe with."""

    def handles(self, plotter, bounds=(-20.0, 20.0, -20.0, 20.0, 0.0, 40.0)):
        import pyvista as pv

        actor = plotter.add_mesh(pv.Box(bounds=bounds))
        return DragHandles(plotter, actor, bounds, lambda _: None), actor

    def test_there_is_an_arrow_and_a_ring_for_each_axis(self, plotter):
        handles, _ = self.handles(plotter)
        assert len(handles._arrows) == 3
        assert len(handles._rings) == 3

    def test_no_arrow_starts_inside_the_part(self, plotter):
        """The complaint that started this: half of every arrow was buried.

        An arrow whose inner end is inside the geometry cannot be aimed at, and
        needing a depth-offset hack to draw it is what smeared the old handles
        across the model.
        """
        bounds = (-20.0, 20.0, -20.0, 20.0, 0.0, 40.0)
        handles, _ = self.handles(plotter, bounds)

        for index, arrow in enumerate(handles._arrows):
            lowest = arrow.GetBounds()[index * 2]
            assert lowest >= bounds[index * 2 + 1], (
                f"the {'XYZ'[index]} arrow starts at {lowest}, inside the part"
            )

    def test_the_arrows_reach_far_enough_to_aim_at(self, plotter):
        handles, _ = self.handles(plotter)
        for index, arrow in enumerate(handles._arrows):
            span = arrow.GetBounds()[index * 2 + 1] - arrow.GetBounds()[index * 2]
            assert span > 5.0

    def test_the_rings_turn_about_the_origin_not_the_part(self, plotter):
        """Because that is what a Rotate feature actually does.

        ``Rotate`` compiles to build123d's ``Rot``, which turns the part about
        the world origin. Pivoting the preview anywhere else shows one thing
        and rebuilds another.
        """
        handles, _ = self.handles(plotter, (-20.0, 20.0, -20.0, 20.0, 0.0, 40.0))
        assert handles._pivot == pytest.approx([0.0, 0.0, 0.0])

    def test_a_rotation_carries_no_translation_with_it(self, plotter):
        """Which is what pivoting on the origin buys.

        Turning about the part's own centre produces a matrix whose
        translation column is non-zero, and that reaches the feature tree as a
        spurious Move alongside the Rotate.
        """
        handles, _ = self.handles(plotter)
        step = handles._step(handles._rings[2], np.pi / 4)
        assert step[:3, 3] == pytest.approx([0.0, 0.0, 0.0])

    def test_a_nudge_along_an_arrow_is_exactly_that_far(self, plotter):
        handles, _ = self.handles(plotter)
        step = handles._step(handles._arrows[1], 12.5)
        assert step[:3, 3] == pytest.approx([0.0, 12.5, 0.0])

    def test_the_ring_radius_is_kept_within_bounds(self, plotter):
        """A part parked far from the origin must not get a gizmo the size of
        the room, and a tiny one must still be grabbable."""
        far, _ = self.handles(plotter, (980.0, 1020.0, -20.0, 20.0, 0.0, 40.0))
        radius = max(abs(value) for value in far._rings[2].GetBounds())
        assert radius <= RING_MOST * far._size * 1.05

        near, _ = self.handles(plotter, (-1.0, 1.0, -1.0, 1.0, 0.0, 2.0))
        smallest = max(abs(value) for value in near._rings[2].GetBounds())
        assert smallest >= RING_LEAST * near._size * 0.95

    def test_moving_the_part_moves_the_handles_with_it(self, plotter):
        """The other half of what the old widget left out.

        Its handle actors were given a transform once, at construction, and
        never updated. The part slid away and the gizmo stayed behind, which
        does not read as a gizmo - it reads as the screen failing to repaint.
        """
        handles, actor = self.handles(plotter)
        handles._apply(handles._step(handles._arrows[2], 30.0))

        assert np.asarray(actor.user_matrix)[2, 3] == pytest.approx(30.0)
        for handle in handles._handles:
            assert np.asarray(handle.user_matrix) == pytest.approx(np.asarray(actor.user_matrix)), (
                "a handle stayed behind while the part moved"
            )

    def test_stopping_takes_every_handle_out_of_the_scene(self, plotter):
        handles, _ = self.handles(plotter)
        names = [name for name in plotter.renderer.actors if name.startswith("drag-")]
        assert len(names) == 6

        handles.stop()
        left = [name for name in plotter.renderer.actors if name.startswith("drag-")]
        assert left == []

    def test_stopping_twice_is_safe(self, plotter):
        handles, _ = self.handles(plotter)
        handles.stop()
        handles.stop()


class TestWhatItLooksLike:
    def test_each_axis_keeps_its_own_colour(self, plotter):
        """Red, green, blue for X, Y, Z - what every other CAD package does."""
        import pyvista as pv

        bounds = (-10.0, 10.0, -10.0, 10.0, 0.0, 20.0)
        actor = plotter.add_mesh(pv.Box(bounds=bounds))
        handles = DragHandles(plotter, actor, bounds, lambda _: None)

        for index, arrow in enumerate(handles._arrows):
            assert arrow.prop.color.hex_rgb.lower() == AXIS_COLOURS[index].lower()

    def test_letting_go_of_a_handle_puts_its_colour_back(self, plotter):
        import pyvista as pv

        bounds = (-10.0, 10.0, -10.0, 10.0, 0.0, 20.0)
        actor = plotter.add_mesh(pv.Box(bounds=bounds))
        handles = DragHandles(plotter, actor, bounds, lambda _: None)

        arrow = handles._arrows[0]
        handles._highlight(arrow)
        assert arrow.prop.color.hex_rgb.lower() != AXIS_COLOURS[0].lower()

        handles._unhighlight(arrow)
        assert arrow.prop.color.hex_rgb.lower() == AXIS_COLOURS[0].lower()


@pytest.mark.renders
class TestDrivenWithRealMouseEvents:
    """The whole thing, through a live window and the Qt event chain.

    Marked ``renders`` for the usual reason, and because it is the only place
    the observers, the picker and the projection are all real at once. It also
    needs a real window system - see the fixture - so it is skipped rather than
    quietly passing under the offscreen platform the rest of the suite uses.

    Note the device pixel ratio. This display scales at 150%, so the VTK render
    window is half again the size of the Qt widget and ``vtkCoordinate`` speaks
    in the former. Getting that wrong sends every synthetic click hundreds of
    pixels from its target and makes a working widget look broken - which is
    exactly what happened while this was being diagnosed.
    """

    @pytest.fixture
    def window(self):
        import os

        from PySide6.QtWidgets import QApplication, QMainWindow
        from pyvistaqt import QtInteractor

        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            pytest.skip(
                "needs a real window system: an embedded VTK render window gets "
                "no surface, and therefore no size, under Qt's offscreen "
                "platform - every coordinate collapses to zero. Run it with "
                "QT_QPA_PLATFORM=windows pytest -m renders"
            )

        app = QApplication.instance() or QApplication([])
        frame = QMainWindow()
        viewport = QtInteractor(frame)
        frame.setCentralWidget(viewport)
        frame.resize(900, 700)
        frame.show()
        _settle(app)
        yield app, frame, viewport
        frame.close()
        _settle(app)

    def test_the_part_stays_under_the_cursor(self, window):
        """The property the whole rewrite exists for.

        Dragged 150 pixels up the screen, the point that was under the pointer
        when the button went down is under it when the button comes up. Within
        a couple of pixels, at two very different zooms.
        """
        app, _frame, viewport = window
        for zoom in (1.0, 2.5):
            dragged = _drag_the_z_arrow(app, viewport, zoom=zoom, pixels=150)
            assert dragged.drift < 3.0, (
                f"at {zoom}x zoom the part drifted {dragged.drift:.1f}px from the cursor"
            )
            assert dragged.moved > 0.5, (
                f"at {zoom}x zoom the part barely moved ({dragged.moved:.2f} mm)"
            )

    def test_how_far_it_moves_depends_on_the_zoom(self, window):
        """Which is the bug, inverted.

        The widget this replaces moved a part the same distance per pixel
        whatever the camera was doing, so zoomed in it crawled. Zoomed in, the
        same drag must now cover *less* of the scene, not the same amount.
        """
        app, _frame, viewport = window
        close_up = _drag_the_z_arrow(app, viewport, zoom=2.5, pixels=150).moved
        wide = _drag_the_z_arrow(app, viewport, zoom=1.0, pixels=150).moved

        assert wide > close_up * 2.0, (
            f"150 px moved {close_up:.1f} mm zoomed in and {wide:.1f} mm zoomed out - "
            "the drag is not tracking the scene"
        )

    def test_a_drag_does_not_also_orbit_the_camera(self, window):
        """Grabbing a handle has to stop the event reaching the camera controls."""
        app, _frame, viewport = window
        dragged = _drag_the_z_arrow(app, viewport, zoom=1.0, pixels=120)

        assert dragged.camera_after == pytest.approx(dragged.camera_before, rel=1e-6), (
            "the drag orbited the view as well as moving the part"
        )

    def test_a_ring_can_still_be_grabbed(self, window):
        """Arrows win where both are under the cursor - rings must still work.

        Putting the rings clear of the arrow heads and letting arrows win the
        pick are both there to stop a click meant for an arrow turning the
        part. Neither may cost the ability to turn it on purpose.
        """
        import pyvista as pv

        app, _frame, viewport = window
        bounds = (-20.0, 20.0, -20.0, 20.0, 0.0, 40.0)
        actor = viewport.add_mesh(pv.Box(bounds=bounds))
        handles = DragHandles(viewport, actor, bounds, lambda _: None)
        viewport.view_isometric()
        viewport.reset_camera()
        viewport.render()
        _settle(app)

        ring = handles._rings[2]
        radius = max(abs(value) for value in ring.GetBounds()[:2])
        # Out at 45 degrees, where no arrow runs.
        point = np.array([radius * np.cos(np.pi / 4), radius * np.sin(np.pi / 4), 0.0])

        found = _pick_at(app, viewport, handles, point)
        assert found is ring, f"aiming at the Z ring found {found}"
        handles.stop()

    def test_releasing_reports_the_whole_drag_once(self, window):
        app, _frame, viewport = window
        seen: list[object] = []
        _drag_the_z_arrow(app, viewport, zoom=1.0, pixels=120, on_release=seen.append)

        assert len(seen) == 1


def _pixel_ratio(app, viewport, widget, seconds: float = 5.0) -> float:
    """How many device pixels the render window puts in one logical Qt pixel.

    This display scales at 150%, and ``vtkCoordinate`` answers in device
    pixels while Qt mouse events are in logical ones. Everything below depends
    on converting between them.

    Both sizes can still be zero well after ``show`` under pytest, so this
    waits for them rather than dividing by nothing - and falls back to what Qt
    itself reports for the screen if VTK never gets there.
    """
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        wide = viewport.render_window.GetSize()[0]
        if widget.width() > 0 and wide > 0:
            return float(wide) / widget.width()
        time.sleep(0.01)

    assert widget.width() > 0, "the viewport never got a size, so nothing here can be measured"
    return float(widget.devicePixelRatioF())


def _pick_at(app, viewport, handles, world):
    """Move the pointer to a world position and report what it is over."""
    import vtk
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    widget = viewport.interactor
    ratio = _pixel_ratio(app, viewport, widget)
    coordinate = vtk.vtkCoordinate()
    coordinate.SetCoordinateSystemToWorld()
    coordinate.SetValue(*world)
    x, y = coordinate.GetComputedDoubleDisplayValue(viewport.renderer)
    position = QPointF(x / ratio, widget.height() - y / ratio)
    assert 0 <= position.x() <= widget.width() and 0 <= position.y() <= widget.height(), (
        "that point is off screen, so this measures nothing"
    )

    app.sendEvent(
        widget,
        QMouseEvent(
            QEvent.Type.MouseMove,
            position,
            widget.mapToGlobal(position.toPoint()),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    _settle(app, 0.05)
    return handles._hovered


def _settle(app, seconds: float = 0.35) -> None:
    import time

    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


class Dragged(NamedTuple):
    """What one synthetic drag amounted to."""

    drift: float
    """How far, in screen pixels, the grabbed point ended up from the cursor."""

    moved: float
    """How far the part travelled, in millimetres."""

    camera_before: tuple
    camera_after: tuple


def _drag_the_z_arrow(app, viewport, zoom, pixels, on_release=None) -> Dragged:
    """Grab the +Z arrow and pull it straight up the screen."""
    import pyvista as pv
    import vtk
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    viewport.clear()
    bounds = (-20.0, 20.0, -20.0, 20.0, 0.0, 40.0)
    actor = viewport.add_mesh(pv.Box(bounds=bounds))
    handles = DragHandles(viewport, actor, bounds, on_release or (lambda _: None))
    # Framed with the handles in the scene, so the arrows are on screen to be
    # aimed at rather than somewhere above the window.
    viewport.view_isometric()
    viewport.reset_camera()
    viewport.camera.zoom(zoom)
    viewport.render()
    _settle(app)

    widget = viewport.interactor
    _pixel_ratio(app, viewport, widget)

    def to_qt(world):
        # Read the ratio again every time rather than once at the top. A
        # pending resize delivered mid-test changes it, and a stale one puts
        # every click in the wrong place - which showed up here as a drag that
        # tracked nothing, intermittently and only after a long run.
        ratio = viewport.render_window.GetSize()[0] / widget.width()
        coordinate = vtk.vtkCoordinate()
        coordinate.SetCoordinateSystemToWorld()
        coordinate.SetValue(*world)
        x, y = coordinate.GetComputedDoubleDisplayValue(viewport.renderer)
        return QPointF(x / ratio, widget.height() - y / ratio)

    def send(kind, position, buttons=Qt.MouseButton.NoButton, button=Qt.MouseButton.NoButton):
        app.sendEvent(
            widget,
            QMouseEvent(
                kind,
                position,
                widget.mapToGlobal(position.toPoint()),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        _settle(app, 0.03)

    grabbed_at = np.array(handles._arrows[2].GetCenter())
    start = to_qt(grabbed_at)
    assert 0 <= start.x() <= widget.width() and 0 <= start.y() <= widget.height(), (
        "the arrow is off screen, so this measures nothing"
    )

    # Keep the whole drag inside the window, and go whichever way has room for
    # it. A pointer driven past an edge has its position clamped, so the last
    # events land somewhere other than where they were aimed and the part looks
    # as though it stopped keeping up - which showed up as a tracking failure of
    # several hundred pixels, at one zoom level only. Zoomed in, the +Z arrow is
    # thirty pixels from the top of the window and there is nowhere to pull it.
    above = start.y() - EDGE_MARGIN
    below = widget.height() - EDGE_MARGIN - start.y()
    way = -1.0 if above >= below else 1.0
    pixels = min(pixels, max(above, below))
    assert pixels >= 60, (
        f"no room to drag: arrow at ({start.x():.0f},{start.y():.0f}) in a "
        f"{widget.width()}x{widget.height()} viewport at zoom {zoom}"
    )

    camera_before = tuple(viewport.camera.position)
    send(QEvent.Type.MouseMove, start)
    send(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert handles._held is handles._arrows[2], "the arrow was not grabbed"

    for step in range(1, 9):
        send(
            QEvent.Type.MouseMove,
            QPointF(start.x(), start.y() + way * pixels * step / 8),
            Qt.MouseButton.LeftButton,
        )

    moved = float(np.asarray(actor.user_matrix)[2, 3])
    target = QPointF(start.x(), start.y() + way * pixels)
    now = to_qt(grabbed_at + np.array([0.0, 0.0, moved]))
    drift = float(np.hypot(now.x() - target.x(), now.y() - target.y()))

    send(
        QEvent.Type.MouseButtonRelease,
        target,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    camera_after = tuple(viewport.camera.position)
    handles.stop()
    return Dragged(drift, abs(moved), camera_before, camera_after)


class TestHandingADragOverToTheFeatureTree:
    """What happens between letting go and the rebuild arriving.

    Reported as: "when you release, the view repaints where it came from" and
    then a second drag landing where the *first* one had put it.

    Both are this interval. A rebuild is an OCCT subprocess and takes a couple
    of seconds; the actor's transform used to be thrown away the instant the
    button came up, so for the whole of that the part sat back where it had
    started. And the handles kept accumulating across drags, so a second drag
    begun before the first had landed started from a transform the feature
    tree was already about to account for.
    """

    def scene(self, plotter):
        from modelpop.domain.printer import PrinterProfile
        from modelpop.rendering import ViewportScene

        from .strategies import unit_cube

        scene = ViewportScene(plotter, PrinterProfile.p2s())
        scene.show_mesh(unit_cube(40))
        return scene

    def test_letting_go_can_leave_the_part_where_it_was_dragged(self, plotter):
        """The snap-back, as a test."""
        scene = self.scene(plotter)
        scene.start_dragging(lambda _: None)
        scene._drag_widget._apply(scene._drag_widget._step(scene._drag_widget._arrows[2], 12.0))

        scene.stop_dragging(keep_where_it_was_dragged=True)

        assert np.asarray(scene._model_actor.user_matrix)[2, 3] == pytest.approx(12.0), (
            "the part sprang back to where it started before its move had been recorded"
        )

    def test_by_default_letting_go_still_puts_it_back(self, plotter):
        """Because a drag that recorded nothing has nothing coming to replace it."""
        scene = self.scene(plotter)
        scene.start_dragging(lambda _: None)
        scene._drag_widget._apply(scene._drag_widget._step(scene._drag_widget._arrows[2], 12.0))

        scene.stop_dragging()

        assert np.asarray(scene._model_actor.user_matrix) == pytest.approx(np.eye(4))

    def test_new_geometry_arrives_with_no_transform_on_it(self, plotter):
        """The other half: the rebuilt part already stands where it was put.

        PyVista reuses the actor registered under a name, so a transform left
        on it would be applied on top of geometry that has already moved - and
        the part would travel twice as far as it was dragged.
        """
        from .strategies import unit_cube

        scene = self.scene(plotter)
        scene.start_dragging(lambda _: None)
        scene._drag_widget._apply(scene._drag_widget._step(scene._drag_widget._arrows[2], 12.0))
        scene.stop_dragging(keep_where_it_was_dragged=True)

        scene.show_mesh(unit_cube(40))

        assert np.asarray(scene._model_actor.user_matrix) == pytest.approx(np.eye(4))

    def test_putting_the_part_back_also_resets_the_handles(self, plotter):
        """Otherwise the next drag starts from the last one's total.

        The handles carry the same matrix as the part and accumulate it across
        drags. Resetting only the part leaves them out of step, and the next
        drag adds to a movement the feature tree has already recorded.
        """
        scene = self.scene(plotter)
        scene.start_dragging(lambda _: None)
        handles = scene._drag_widget
        handles._apply(handles._step(handles._arrows[2], 12.0))

        scene.forget_drag()

        assert np.asarray(handles._matrix) == pytest.approx(np.eye(4))
        for handle in handles._handles:
            assert np.asarray(handle.user_matrix) == pytest.approx(np.eye(4))

    def test_a_second_drag_after_a_reset_starts_from_nothing(self, plotter):
        """The compounding bug, stated as the number it produces."""
        scene = self.scene(plotter)
        scene.start_dragging(lambda _: None)
        handles = scene._drag_widget

        handles._apply(handles._step(handles._arrows[2], 12.0))
        scene.forget_drag()
        handles._apply(handles._step(handles._arrows[2], 5.0))

        assert np.asarray(scene._model_actor.user_matrix)[2, 3] == pytest.approx(5.0), (
            "the second drag carried the first one's movement with it"
        )
