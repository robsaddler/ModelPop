"""Drawing the scene, and how often it is worth doing.

Building VTK polydata and shading normals for a large mesh is real work and it
can only happen on the interface thread - so every avoidable redraw is a
window that stops answering. One change reaches the viewport more than once:
the workspace announces a new model, the scene adopts the same geometry back
as a body and announces in turn, and both handlers redraw.

Measured on the 1.1 million triangle dragon, simplifying it drew the result
three times: 0.43 s, 0.15 s and 0.13 s. That is what "Simplify locks the UI"
was made of, along with a status bar that never got a turn to paint.
"""

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


class TestDrawingTheSceneOnce:
    """One change reaches the viewport more than once. It must draw once.

    The workspace announces a new model, the scene adopts the same geometry
    back as a body and announces in turn, and both handlers redraw. Measured
    on the 1.1 million triangle dragon: simplifying it drew the result three
    times, at 0.43 s, 0.15 s and 0.13 s - every one of them on the interface
    thread, because building VTK polydata and shading normals is not work a
    worker can take. That is what "Simplify locks the UI" was made of, along
    with a status bar that never got a turn to paint.

    Marked ``renders``: this needs a real viewport (CLAUDE.md, trap 8).
    """

    def window(self, app):
        from modelpop.application.workspace import Workspace
        from modelpop.mesh import TrimeshIO, TrimeshOps
        from modelpop.ui.main_window import MainWindow

        return MainWindow(Workspace(TrimeshIO(), TrimeshOps()))

    def drawing(self, window):
        """How many times the viewport is actually asked to build actors."""
        drawn = []
        for name in ("show_bodies", "show_mesh"):
            real = getattr(window._scene, name)

            def counted(*a, _real=real, _name=name, **k):
                drawn.append(_name)
                return _real(*a, **k)

            setattr(window._scene, name, counted)
        return drawn

    @pytest.mark.renders
    def test_the_same_scene_is_not_drawn_twice(self, app):
        window = self.window(app)
        try:
            drawn = self.drawing(window)
            window._draw_scene()
            window._draw_scene()
            window._draw_scene()

            assert len(drawn) == 1, f"one scene was drawn {len(drawn)} times"
        finally:
            window.close()

    @pytest.mark.renders
    def test_a_scene_that_differs_is_drawn(self, app):
        """Skipping must never cost a redraw that changes what is on screen.

        The readiness verdict is part of what a draw puts up - a model with
        blockers is drawn in the problem colour - so a change to it has to get
        through even when the geometry has not moved.
        """
        window = self.window(app)
        try:
            window._draw_scene()
            drawn = self.drawing(window)

            window._draw_scene()
            assert drawn == [], "the identical scene was drawn again"

            window._draw_scene(has_problems=True)
            assert len(drawn) == 1, "a scene drawn in a different colour was skipped"
        finally:
            window.close()

    @pytest.mark.renders
    def test_a_drag_forces_the_next_draw(self, app):
        """The actor is left where it was dragged, so the next draw must run."""
        window = self.window(app)
        try:
            window._draw_scene()
            drawn = self.drawing(window)

            window._drawn = None  # what _dragged does when it hands over
            window._draw_scene()

            assert len(drawn) == 1
        finally:
            window.close()


class TestWhatAClickPutsInHand:
    """Clicking past the model must not put down what is in hand.

    "Why is it every time I put handles on the dragon and try to move it or
    make it bigger, it snaps back to where it was!"

    One line did that. A click that hit nothing deselected everything, and the
    drag handles stayed exactly where they were - full brightness, still
    grabbable, still bolted to the part. Dragging one then moved the part on
    screen and was refused on release with "Nothing is selected", so it sprang
    back to where it started. And it stayed that way until something was
    clicked again, which is the "every time".

    Clicking past the model is not rare. An orbit that travels less than the
    four pixels of click slop ends as a click, so it happens constantly.

    Marked ``renders``: picking needs a real viewport (CLAUDE.md, trap 8).
    """

    def window(self, app):
        from modelpop.application.modelling import ModellingSession
        from modelpop.application.workspace import Workspace
        from modelpop.mesh import TrimeshIO, TrimeshOps
        from modelpop.ui.main_window import MainWindow

        return MainWindow(
            Workspace(TrimeshIO(), TrimeshOps()),
            None,
            ModellingSession(mesh_io=TrimeshIO()),
        )

    def with_a_part(self, app):
        import os
        import time

        import numpy as np
        import trimesh
        from PySide6.QtWidgets import QApplication

        from modelpop.domain.mesh import Mesh

        if os.environ.get("QT_QPA_PLATFORM", "offscreen") == "offscreen":
            pytest.skip(
                "needs a real window system: an embedded VTK render window gets "
                "no surface, and therefore no size, under Qt's offscreen "
                "platform, so nothing can be picked. Run it with "
                "QT_QPA_PLATFORM=windows pytest -m renders"
            )

        window = self.window(app)
        # Shown, and given a size: an embedded VTK window with no surface picks
        # nothing at all, and a test that cannot pick proves nothing.
        window.resize(1100, 850)
        window.show()
        QApplication.processEvents()

        shape = trimesh.creation.box(extents=(20.0, 20.0, 20.0))
        window._modelling.place_mesh(
            Mesh(np.asarray(shape.vertices), np.asarray(shape.faces, np.int32)), "a part"
        )
        until = time.monotonic() + 8.0
        while time.monotonic() < until and not window._modelling.bodies:
            QApplication.processEvents()
            time.sleep(0.01)
        for _ in range(50):
            QApplication.processEvents()
        return window

    def where_the_part_is(self, window) -> tuple[float, float]:
        """The part's centre, as a point in the viewport widget.

        Asked of VTK's own projection rather than assumed to be the middle of
        the widget: the resting view looks into a 256 mm printer from slightly
        above, so a part standing on the plate sits well below centre. Aiming at
        the middle and getting nothing is a test measuring its own arithmetic.

        Two conversions, both easy to get wrong. VTK counts rows from the
        bottom and Qt from the top; and this display scales at 150%, so VTK
        speaks in device pixels where Qt mouse events are logical ones
        (CLAUDE.md, trap 11).
        """
        import vtk

        box = window._modelling.bodies[0].bounds
        centre = (
            (box.min_x + box.max_x) / 2,
            (box.min_y + box.max_y) / 2,
            (box.min_z + box.max_z) / 2,
        )
        at = vtk.vtkCoordinate()
        at.SetCoordinateSystemToWorld()
        at.SetValue(*centre)
        x, y = at.GetComputedDoubleDisplayValue(window._viewport.renderer)
        ratio = window._device_ratio()
        return x / ratio, window._viewport.interactor.height() - y / ratio

    @pytest.mark.renders
    def test_a_click_that_hits_nothing_keeps_what_is_in_hand(self, app):
        window = self.with_a_part(app)
        try:
            assert window._modelling.selected, "nothing was in hand to begin with"
            in_hand = window._modelling.selected

            # Far off in the corner of the viewport, where the part is not.
            window._select_at(2.0, 2.0)

            assert window._modelling.selected == in_hand
        finally:
            window.close()

    @pytest.mark.renders
    def test_a_click_that_hits_a_part_picks_it_up(self, app):
        """Keeping the selection on a miss must not cost selecting on a hit."""
        window = self.with_a_part(app)
        try:
            window._modelling.select("")
            assert not window._modelling.selected

            window._scene.frame_model()
            window._viewport.render()
            window._select_at(*self.where_the_part_is(window))

            assert window._modelling.selected == "body-1"
        finally:
            window.close()


class TestClearingThePlate:
    """Taking everything off, and being able to find how.

    "I need a clear option to reset the printer to empty - removes all models
    from the canvas."

    The mechanism already worked. What did not was finding it: the only way in
    was a button called **Start again**, at the bottom of a group inside a tab,
    which is neither where anybody would look nor what they would call it. It
    is now *Clear the plate* on the File menu under the standard New shortcut,
    on the right-click menu, and on that button - all three the same words.

    Marked ``renders``: building the window builds a VTK viewport.
    """

    def window(self, app):
        from modelpop.application.modelling import ModellingSession
        from modelpop.application.workspace import Workspace
        from modelpop.mesh import TrimeshIO, TrimeshOps
        from modelpop.ui.main_window import MainWindow

        return MainWindow(
            Workspace(TrimeshIO(), TrimeshOps()), None, ModellingSession(mesh_io=TrimeshIO())
        )

    def with_a_model(self, window):
        import time

        import numpy as np
        import trimesh
        from PySide6.QtWidgets import QApplication

        from modelpop.domain.mesh import Mesh

        shape = trimesh.creation.box(extents=(30.0, 20.0, 20.0))
        window._view_model.adopt(
            Mesh(np.asarray(shape.vertices), np.asarray(shape.faces, np.int32))
        )
        until = time.monotonic() + 8.0
        while time.monotonic() < until and not window._modelling.bodies:
            QApplication.processEvents()
            time.sleep(0.01)
        for _ in range(60):
            QApplication.processEvents()
        return window

    @pytest.mark.renders
    def test_it_is_on_the_file_menu_under_the_new_shortcut(self, app):
        from PySide6.QtGui import QKeySequence

        window = self.window(app)
        try:
            assert window._clear_action.text().replace("&", "") == "Clear the plate"
            assert window._clear_action.shortcut() == QKeySequence(QKeySequence.StandardKey.New)
        finally:
            window.close()

    @pytest.mark.renders
    def test_it_is_on_the_right_click_menu_too(self, app):
        window = self.window(app)
        try:
            assert window._clear_here_action.text().replace("&", "") == "Clear the plate"
        finally:
            window.close()

    @pytest.mark.renders
    def test_an_empty_plate_is_not_worth_a_question(self, app):
        """Asked twice a session about a plate that is already empty is noise."""
        window = self.window(app)
        try:
            window._clear_the_plate()  # would block on a dialog if one were shown

            assert "already empty" in window.statusBar().currentMessage()
        finally:
            window.close()

    @pytest.mark.renders
    def test_it_knows_when_there_is_something_to_clear(self, app):
        window = self.with_a_model(self.window(app))
        try:
            assert window._anything_on_the_plate()
        finally:
            window.close()

    @pytest.mark.renders
    def test_clearing_empties_the_scene_and_the_workspace_together(self, app):
        """Half-cleared is worse than not cleared: the readiness panel would go
        on describing a model that is no longer on screen."""
        window = self.with_a_model(self.window(app))
        try:
            window._modelling.clear()
            for _ in range(80):
                from PySide6.QtWidgets import QApplication

                QApplication.processEvents()

            assert window._modelling.bodies == ()
            assert not window._view_model.state.has_model
            assert not window._anything_on_the_plate()
        finally:
            window.close()
