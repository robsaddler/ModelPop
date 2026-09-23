"""Measuring between two points, with no viewport and no display.

The whole interaction lives in a state machine on purpose: turning it on,
clicking, missing, clicking again, reading the answer and starting over are all
testable in microseconds, and none of it needs VTK. The viewport's job is to
turn a click into a point in millimetres, which is tested separately against
real geometry.
"""

import math

import pytest

from modelpop.presentation.measuring import Measurement, MeasuringTool, Step

ORIGIN = (0.0, 0.0, 0.0)
ACROSS = (30.0, 0.0, 0.0)
DIAGONAL = (30.0, 40.0, 0.0)


def measuring() -> MeasuringTool:
    tool = MeasuringTool()
    tool.turn_on()
    return tool


class TestTheAnswer:
    def test_a_straight_run_is_its_own_length(self):
        assert Measurement(ORIGIN, ACROSS).distance == pytest.approx(30.0)

    def test_a_diagonal_is_measured_through_the_air(self):
        assert Measurement(ORIGIN, DIAGONAL).distance == pytest.approx(50.0)

    def test_it_is_broken_down_by_axis_as_well(self):
        """ "How tall" and "how far apart" are different questions."""
        measured = Measurement(ORIGIN, (30.0, 40.0, 120.0))

        assert measured.across == pytest.approx(30.0)
        assert measured.back == pytest.approx(40.0)
        assert measured.up == pytest.approx(120.0)

    def test_direction_does_not_change_any_of_it(self):
        there = Measurement(ORIGIN, DIAGONAL)
        back = Measurement(DIAGONAL, ORIGIN)

        assert there.distance == pytest.approx(back.distance)
        assert there.across == pytest.approx(back.across)

    def test_two_points_in_the_same_place_measure_nothing(self):
        assert Measurement(ORIGIN, ORIGIN).distance == 0.0

    def test_it_says_the_straight_line_and_the_axes(self):
        told = Measurement(ORIGIN, DIAGONAL).describe()

        assert "50.00 mm apart" in told
        assert "30.00 across" in told
        assert "40.00 back" in told


class TestTheInteraction:
    def test_it_starts_switched_off(self):
        tool = MeasuringTool()

        assert not tool.is_on
        assert tool.step is Step.OFF
        assert tool.measurement is None

    def test_clicks_do_nothing_while_it_is_off(self):
        tool = MeasuringTool()

        assert not tool.picked(ORIGIN)
        assert tool.measurement is None

    def test_two_clicks_make_a_measurement(self):
        tool = measuring()
        before = tool.step

        took_the_first = tool.picked(ORIGIN)
        after_one = tool.step
        took_the_second = tool.picked(DIAGONAL)

        assert before is Step.FIRST
        assert took_the_first and after_one is Step.SECOND
        assert took_the_second and tool.step is Step.DONE
        assert tool.measurement is not None
        assert tool.measurement.distance == pytest.approx(50.0)

    def test_a_click_that_missed_is_ignored_rather_than_resetting(self):
        """Losing half a measurement to a stray pixel is the worst it could do."""
        tool = measuring()
        tool.picked(ORIGIN)

        assert not tool.picked(None)
        assert tool.step is Step.SECOND, "still waiting for the second point"

        tool.picked(DIAGONAL)
        assert tool.measurement is not None

    def test_a_third_click_starts_the_next_measurement_from_there(self):
        """What somebody measuring several things in a row expects."""
        tool = measuring()
        tool.picked(ORIGIN)
        tool.picked(DIAGONAL)

        tool.picked(ACROSS)

        assert tool.step is Step.SECOND
        assert tool.measurement is None
        assert tool.points == (ACROSS,)

    def test_starting_again_keeps_it_switched_on(self):
        tool = measuring()
        tool.picked(ORIGIN)
        tool.picked(DIAGONAL)

        tool.start_again()

        assert tool.is_on
        assert tool.step is Step.FIRST
        assert tool.points == ()

    def test_starting_again_while_off_does_nothing(self):
        tool = MeasuringTool()
        tool.start_again()
        assert tool.step is Step.OFF

    def test_switching_off_forgets_what_was_measured(self):
        tool = measuring()
        tool.picked(ORIGIN)
        tool.picked(DIAGONAL)

        tool.turn_off()

        assert tool.measurement is None
        assert tool.points == ()

    def test_switching_on_again_starts_from_nothing(self):
        tool = measuring()
        tool.picked(ORIGIN)

        tool.turn_on()

        assert tool.step is Step.FIRST

    def test_the_points_are_handed_over_for_drawing_as_they_arrive(self):
        tool = measuring()
        nothing_yet = tool.points

        tool.picked(ORIGIN)
        after_one = tool.points

        tool.picked(DIAGONAL)
        after_two = tool.points

        assert nothing_yet == ()
        assert after_one == (ORIGIN,)
        assert after_two == (ORIGIN, DIAGONAL)


class TestWhatItSays:
    def test_every_step_says_something_useful(self):
        tool = MeasuringTool()
        assert "off" in tool.describe()

        tool.turn_on()
        assert "Click a point" in tool.describe()

        tool.picked(ORIGIN)
        assert "second point" in tool.describe()

        tool.picked(DIAGONAL)
        assert "50.00 mm apart" in tool.describe()

    def test_a_finished_measurement_says_how_to_start_the_next(self):
        tool = measuring()
        tool.picked(ORIGIN)
        tool.picked(DIAGONAL)

        assert "measure something else" in tool.describe()

    def test_a_real_distance_agrees_with_the_arithmetic(self):
        """Guards against a describe() that formats the wrong number."""
        tool = measuring()
        tool.picked((10.0, 20.0, 30.0))
        tool.picked((40.0, 60.0, 30.0))

        expected = math.dist((10.0, 20.0, 30.0), (40.0, 60.0, 30.0))
        assert f"{expected:.2f} mm apart" in tool.describe()
