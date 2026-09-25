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
