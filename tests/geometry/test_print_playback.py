"""Watching a print back, and how fast it plays.

"Changing print speed on watch it print seems to have no effect?"

It had none, and only on the prints anybody would watch. A tick of playback is
a fraction of a slider step on anything long: a four-hour print advances 0.069
of a step per tick at 30x and 0.275 at 120x. Both truncate to zero, and the
floor of one step per tick that kept playback moving at all then made every
speed identical. Only a print under about twenty minutes ever showed a
difference.

Driven by calling the advance directly rather than by waiting for a timer: what
is being tested is the arithmetic, and a test that watched a four-hour print
play would take four hours divided by nothing much.
"""

import pytest
from PySide6.QtWidgets import QApplication

from modelpop.domain.printer import PrinterProfile
from modelpop.ui.print_window import _SPEEDS, _STEPS, PrintWindow


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def a_print(tmp_path, minutes: int):
    """A G-code file that claims to take a given time, and a window over it."""
    lines = [f"; total estimated time: {minutes}m 0s", "M83", "G90"]
    for layer in range(6):
        lines += [
            "; CHANGE_LAYER",
            f"; Z_HEIGHT: {0.2 * (layer + 1)}",
            "; FEATURE: Outer wall",
            "G1 X90 Y90 F9000",
            "G1 X110 Y90 E1.0",
            "G1 X110 Y110 E1.0",
            "G1 X90 Y110 E1.0",
            "G1 X90 Y90 E1.0",
        ]
    path = tmp_path / "plate_1.gcode"
    path.write_text("\n".join(lines), encoding="utf-8")
    return PrintWindow(path, PrinterProfile.p2s())


def how_far(window, speed: float, ticks: int = 40) -> int:
    """Where the slider ends up after so many ticks at a given speed."""
    while window._speed != speed:
        window._cycle_speed()
    window._seek(0)
    for _ in range(ticks):
        window._advance()
    return window._slider.value()


class TestTheSpeedActuallyChangesTheSpeed:
    @pytest.mark.renders
    def test_each_speed_covers_more_ground_than_the_last(self, app, tmp_path):
        """On a four-hour print, which is the case that was broken."""
        window = a_print(tmp_path, minutes=240)
        try:
            covered = [how_far(window, speed) for speed in _SPEEDS]

            assert covered == sorted(covered), f"the speeds went {covered}"
            assert len(set(covered)) == len(covered), (
                f"every speed played at the same rate: {covered}"
            )
        finally:
            window.close()

    @pytest.mark.renders
    def test_the_speeds_are_in_proportion(self, app, tmp_path):
        """Four times the speed covers four times the print.

        Measured over enough ticks that the slider's own integer steps do not
        dominate: forty ticks at 30x on a four-hour print comes to 2.75 steps,
        and comparing 2 against 11 says more about rounding than about speed.
        """
        window = a_print(tmp_path, minutes=240)
        try:
            ticks = 400
            slow = how_far(window, _SPEEDS[0], ticks)
            faster = how_far(window, _SPEEDS[1], ticks)
            ratio = _SPEEDS[1] / _SPEEDS[0]

            assert faster == pytest.approx(slow * ratio, rel=0.05)
        finally:
            window.close()

    @pytest.mark.renders
    def test_a_long_print_still_moves_at_all(self, app, tmp_path):
        """The floor that hid the bug was there for a reason: do not lose it."""
        window = a_print(tmp_path, minutes=720)
        try:
            assert how_far(window, _SPEEDS[0], ticks=200) > 0
        finally:
            window.close()

    @pytest.mark.renders
    def test_playing_to_the_end_stops_there(self, app, tmp_path):
        window = a_print(tmp_path, minutes=1)
        try:
            for _ in range(500):
                window._advance()

            assert window._slider.value() == _STEPS
            assert not window._ticker.isActive()
        finally:
            window.close()


class TestScrubbingByHand:
    @pytest.mark.renders
    def test_dragging_the_slider_moves_where_playback_carries_on_from(self, app, tmp_path):
        """Otherwise the scrub is undone a tick later.

        Playback keeps its position as a float, and it has to follow the slider
        when the slider is the thing being moved.
        """
        window = a_print(tmp_path, minutes=240)
        try:
            how_far(window, _SPEEDS[0])
            window._slider.setValue(700)
            window._advance()

            assert window._slider.value() >= 700
        finally:
            window.close()
