"""The timer at the end of the status bar.

"Repairing the mesh..." tells you the app is alive. It does not tell you
whether to wait, and the answer here ranges from well under a second to a
minute and a half on a million triangles. The number was already being shown
for a CAD rebuild and for nothing else, so the two slowest things in the
application - repairing and downloading - ran in silence.

Time is injected throughout. A test that measures a duration by sleeping
through it is a test that makes the suite slower for no information.
"""

import pytest
from PySide6.QtWidgets import QApplication, QStatusBar

from modelpop.ui.how_long import HowLong, spoken_duration


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


class FakeClock:
    """A monotonic clock that only moves when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def pass_by(self, seconds: float) -> None:
        self.now += seconds


def a_timer() -> tuple[HowLong, FakeClock]:
    clock = FakeClock()
    return HowLong(clock=clock), clock


class TestCountingAJob:
    def test_it_starts_at_nothing(self, app):
        timer, _clock = a_timer()
        assert timer.seconds == 0.0
        assert not timer.running

    def test_it_counts_while_something_is_working(self, app):
        timer, clock = a_timer()
        timer.busy(True, "Repairing the mesh")
        clock.pass_by(4.25)

        assert timer.running
        assert timer.seconds == pytest.approx(4.25)

    def test_the_figure_is_shown_not_just_held(self, app):
        timer, clock = a_timer()
        timer.busy(True, "Repairing the mesh")
        clock.pass_by(4.25)
        timer._show_the_time()

        assert timer.text() == "4.2s"

    def test_it_stops_when_the_work_does(self, app):
        timer, clock = a_timer()
        timer.busy(True)
        clock.pass_by(3.0)
        timer.busy(False)
        clock.pass_by(90.0)

        assert not timer.running
        assert timer.seconds == pytest.approx(3.0), "it kept counting after the work stopped"

    def test_the_last_figure_stays_up(self, app):
        """Asked afterwards, "how long did that take?" is still answerable."""
        timer, clock = a_timer()
        timer.busy(True)
        clock.pass_by(12.0)
        timer.busy(False)

        assert timer.text() == "12.0s"

    def test_a_new_job_starts_from_zero(self, app):
        timer, clock = a_timer()
        timer.busy(True)
        clock.pass_by(12.0)
        timer.busy(False)

        timer.busy(True)
        assert timer.seconds == pytest.approx(0.0)


class TestTwoJobsAtOnce:
    """Two view-models work independently and either can be running.

    The window hands over their combined state, so what is timed is the span
    the application is busy - which is what somebody is actually waiting for.
    """

    def test_being_told_busy_again_does_not_restart_it(self, app):
        timer, clock = a_timer()
        timer.busy(True, "Rebuilding")
        clock.pass_by(5.0)
        timer.busy(True, "Rebuilding")
        clock.pass_by(1.0)

        assert timer.seconds == pytest.approx(6.0), "a second job restarted the clock"

    def test_being_told_idle_again_does_not_restart_it(self, app):
        timer, clock = a_timer()
        timer.busy(True)
        clock.pass_by(5.0)
        timer.busy(False)
        timer.busy(False)

        assert timer.seconds == pytest.approx(5.0)


class TestSayingIt:
    @pytest.mark.parametrize(
        ("seconds", "said"),
        [
            (0.0, "0.0s"),
            (0.04, "0.0s"),
            (1.0, "1.0s"),
            (12.34, "12.3s"),
            (59.9, "59.9s"),
            (60.0, "1m 00s"),
            (96.3, "1m 36s"),
            (3600.0, "60m 00s"),
        ],
    )
    def test_a_duration_reads_the_way_it_would_be_spoken(self, seconds, said):
        assert spoken_duration(seconds) == said

    def test_a_negative_duration_cannot_happen_but_reads_sanely_anyway(self):
        """A clock going backwards must not put "-0.0s" in front of anyone."""
        assert spoken_duration(-1.0) == "0.0s"


class TestWhatItSays:
    def test_it_names_what_is_running(self, app):
        timer, clock = a_timer()
        timer.busy(True, "Repairing the mesh")
        clock.pass_by(8.0)
        timer._show_the_time()

        assert "Repairing the mesh" in timer.toolTip()
        assert "so far" in timer.toolTip()

    def test_afterwards_it_says_what_took_that_long(self, app):
        timer, clock = a_timer()
        timer.busy(True, "Repairing the mesh")
        clock.pass_by(8.0)
        timer.busy(False)

        assert timer.toolTip() == "Repairing the mesh took 8.0s."


class TestWhereItSits:
    def test_a_message_cannot_paint_over_it(self, app):
        """Added permanently, at the right-hand end.

        An ordinary status message occupies the same strip and would cover a
        widget placed in it, which would hide the timer exactly when something
        slow was announcing itself.
        """
        bar = QStatusBar()
        timer, _clock = a_timer()
        bar.addPermanentWidget(timer)
        bar.showMessage("Repairing the mesh...")

        assert timer.parent() is not None
        assert not timer.isHidden()


class TestTheWindowUsesIt:
    """Marked ``renders``: building the window builds a VTK viewport, which
    takes a GPU-less runner down rather than failing (see CLAUDE.md, trap 8).
    """

    def window(self, app):
        from modelpop.application.workspace import Workspace
        from modelpop.mesh import TrimeshIO, TrimeshOps
        from modelpop.ui.main_window import MainWindow

        return MainWindow(Workspace(TrimeshIO(), TrimeshOps()))

    @pytest.mark.renders
    def test_the_clock_is_at_the_end_of_the_status_bar(self, app):
        window = self.window(app)
        try:
            assert isinstance(window._how_long, HowLong)
            assert window._how_long.parent() is not None
            assert not window._how_long.running, "it was counting before anything ran"
        finally:
            window.close()

    @pytest.mark.renders
    def test_it_follows_the_combined_state_of_both_view_models(self, app):
        """One half finishing while the other runs must not stop the clock."""
        window = self.window(app)
        try:
            window._view_model._set_busy(True)
            assert window._how_long.running

            window._modelling._set_busy(True)
            window._view_model._set_busy(False)
            assert window._how_long.running, "the clock stopped while a rebuild was still going"

            window._modelling._set_busy(False)
            assert not window._how_long.running
        finally:
            window.close()
