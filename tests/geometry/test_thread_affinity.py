"""Everything the window is told must arrive on the interface thread.

This is the test that was missing, and the gap is worth naming: every other
test drives the view-models with the inline runner, so there is never a second
thread and nothing could ever arrive on the wrong one. The real application
rebuilds on a worker, and that is the only configuration where this can break.

It broke. A CAD rebuild finished on its worker, handed the mesh to the
workspace view-model, and the window's listeners ran there - touching Qt
widgets and the VTK render window, whose OpenGL context belongs to the
interface thread alone. The geometry came back drawn wrong and the next orbit
deadlocked the process: fifty-seven threads, all waiting, seven seconds of
processor time between them.
"""

from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


class Announcer(QObject):
    """Stands in for a view-model that announces from wherever it worked."""

    said = Signal(object)


class Worker(QThread):
    """A rebuild, as far as this test is concerned."""

    def __init__(self, announce) -> None:
        super().__init__()
        self._announce = announce

    def run(self) -> None:
        self._announce("done")


class TestWhatCrossesBack:
    def test_a_signal_delivers_on_the_thread_that_owns_the_receiver(self, app):
        """The whole mechanism, in one assertion.

        If this ever stops being true, the window's listeners are running on
        whichever thread did the work, and touching VTK from there is what
        hung the application.
        """
        heard_on: list[int] = []
        announcer = Announcer()
        announcer.said.connect(lambda _: heard_on.append(threading.get_ident()))

        worker = Worker(announcer.said.emit)
        worker.start()
        worker.wait(5000)
        app.processEvents()

        assert heard_on, "nothing was delivered at all"
        assert heard_on[0] == threading.get_ident(), (
            "the listener ran on the worker thread - VTK and Qt widgets cannot "
            "be touched from there"
        )

    def test_a_direct_callback_does_not_cross_back(self, app):
        """Why the bridge is needed, stated as a test rather than a comment.

        This is what the window used to do: hand the view-model a plain Python
        callable. It runs wherever the work finished, which is exactly the bug.
        """
        ran_on: list[int] = []

        worker = Worker(lambda _: ran_on.append(threading.get_ident()))
        worker.start()
        worker.wait(5000)

        assert ran_on
        assert ran_on[0] != threading.get_ident(), (
            "a plain callback happened to run on the main thread, so this test "
            "is no longer demonstrating anything"
        )


class TestTheWindowIsWiredThroughSignals:
    """The window must register signal emitters, never bare methods."""

    def window(self, app):
        from modelpop.application.workspace import Workspace
        from modelpop.mesh import TrimeshIO, TrimeshOps
        from modelpop.ui.main_window import MainWindow

        return MainWindow(Workspace(TrimeshIO(), TrimeshOps()))

    @pytest.mark.renders
    def test_every_announcement_is_a_signal_emit_rather_than_a_method(self, app):
        """Bound methods here would run on the worker. Emitters queue instead.

        Marked ``renders`` because building the window builds a VTK viewport,
        which takes a GPU-less runner down rather than failing.
        """
        window = self.window(app)
        try:
            listeners = [
                *window._view_model._state_listeners,
                *window._view_model._notification_listeners,
                *window._view_model._busy_listeners,
                *window._modelling._outcome_listeners,
            ]

            assert listeners, "the window registered nothing at all"
            for listener in listeners:
                assert "emit" in repr(listener), (
                    f"{listener!r} is not a signal emitter, so it will run on "
                    "whichever thread announced it"
                )
        finally:
            window.close()

    @pytest.mark.renders
    def test_the_signals_belong_to_the_interface_thread(self, app):
        """A queued connection only helps if the receiver lives on the UI thread."""
        window = self.window(app)
        try:
            assert window._signals.thread() == QThread.currentThread()
        finally:
            window.close()

    @pytest.mark.renders
    def test_a_mesh_arriving_from_a_worker_reaches_the_viewport(self, app):
        """End to end: the path that deadlocked, exercised across a real thread."""
        from .strategies import unit_cube

        window = self.window(app)
        try:
            worker = Worker(lambda _: window._view_model.adopt(unit_cube(20)))
            worker.start()
            worker.wait(5000)

            # Nothing has been delivered yet: it is queued, which is the point.
            app.processEvents()

            assert window._view_model.state.has_model
            assert "model" in window._viewport.renderer.actors
        finally:
            window.close()


class TestConnectionTypes:
    def test_auto_connection_is_queued_across_threads(self, app):
        """Qt's default does the right thing, and this pins that assumption."""
        seen: list[str] = []
        announcer = Announcer()
        announcer.said.connect(
            lambda _: seen.append("ran"), Qt.ConnectionType.AutoConnection
        )

        worker = Worker(announcer.said.emit)
        worker.start()
        worker.wait(5000)

        assert seen == [], "it ran before the event loop got a turn - not queued"
        app.processEvents()
        assert seen == ["ran"]
