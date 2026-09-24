"""Reading a drag in the viewport as commands the feature tree understands.

Pure maths over a 4x4 matrix, so the whole thing runs with no graphics context
and no window. What matters here is that a drag becomes ordinary undoable
commands rather than a transform hanging off an actor - and that a drag the
vocabulary cannot express is *refused* rather than rounded to something near
it, because a part quietly rotated onto the wrong axis is a part nobody can
debug by looking at the tree.
"""

import math

import numpy as np
import pytest

from modelpop.domain.cad_commands import Move, Rotate
from modelpop.presentation.dragging import LEAST_MOVE_MM, Drag, movement_in


def shifted(dx: float, dy: float, dz: float) -> np.ndarray:
    grid = np.eye(4)
    grid[0:3, 3] = (dx, dy, dz)
    return grid


def turned(degrees: float, axis: str) -> np.ndarray:
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    spin: list[list[float]] = {
        "X": [[1.0, 0.0, 0.0], [0.0, cos, -sin], [0.0, sin, cos]],
        "Y": [[cos, 0.0, sin], [0.0, 1.0, 0.0], [-sin, 0.0, cos]],
        "Z": [[cos, -sin, 0.0], [sin, cos, 0.0], [0.0, 0.0, 1.0]],
    }[axis]
    grid = np.eye(4)
    grid[0:3, 0:3] = spin
    return grid


class TestReadingAShift:
    def test_a_drag_along_one_axis_becomes_a_move(self):
        drag = movement_in(shifted(12.5, 0, 0))

        assert drag.move == Move(12.5, 0.0, 0.0)
        assert drag.turn is None

    def test_a_drag_in_three_directions_becomes_one_move(self):
        assert movement_in(shifted(3, -4, 5)).move == Move(3.0, -4.0, 5.0)

    def test_a_twitch_is_not_a_command(self):
        """Otherwise the tree fills with steps nobody can see the effect of."""
        assert movement_in(shifted(LEAST_MOVE_MM / 2, 0, 0)).move is None

    def test_a_twitch_in_one_direction_does_not_lose_a_real_move_in_another(self):
        drag = movement_in(shifted(0.001, 20.0, 0))
        assert drag.move is not None
        assert drag.move.dy == pytest.approx(20.0)

    def test_no_drag_at_all_records_nothing(self):
        assert not movement_in(np.eye(4)).did_anything


class TestReadingATurn:
    @pytest.mark.parametrize("axis", ["X", "Y", "Z"])
    @pytest.mark.parametrize("degrees", [15.0, 45.0, 90.0, 137.0])
    def test_a_turn_about_one_axis_comes_back_as_itself(self, axis, degrees):
        drag = movement_in(turned(degrees, axis))

        assert drag.turn is not None
        assert drag.turn.axis == axis
        assert drag.turn.degrees == pytest.approx(degrees, abs=0.01)

    def test_a_turn_the_other_way_comes_back_the_other_way(self):
        """Rotate wraps a negative angle, so -30 reads back as 330."""
        drag = movement_in(turned(-30.0, "Z"))
        assert drag.turn is not None
        assert drag.turn.degrees == pytest.approx(330.0, abs=0.01)

    def test_a_half_turn_is_read_despite_the_usual_route_failing_on_it(self):
        """At 180 degrees the antisymmetric part vanishes and names no axis."""
        drag = movement_in(turned(180.0, "Y"))

        assert drag.turn is not None
        assert drag.turn.axis == "Y"
        assert drag.turn.degrees == pytest.approx(180.0, abs=0.01)

    def test_a_nudge_of_a_rotation_handle_is_not_a_command(self):
        assert movement_in(turned(0.05, "Z")).turn is None

    def test_a_turn_about_two_axes_is_refused_rather_than_rounded(self):
        """The vocabulary says one axis. Rounding moves the part somewhere else."""
        both = turned(30.0, "X") @ turned(30.0, "Y")
        drag = movement_in(both)

        assert drag.turn is None
        assert "more than one axis" in drag.refused

    def test_a_refused_turn_does_not_lose_the_move_that_came_with_it(self):
        both = turned(30.0, "X") @ turned(30.0, "Y")
        both[0:3, 3] = (10.0, 0.0, 0.0)
        drag = movement_in(both)

        assert drag.move == Move(10.0, 0.0, 0.0)
        assert drag.turn is None


class TestRefusingWhatItCannotSay:
    def test_a_matrix_that_scales_is_read_as_a_resize(self):
        """The corner grips scale, so this is an instruction rather than corruption."""
        grid = np.eye(4)
        grid[0:3, 0:3] *= 2.0
        drag = movement_in(grid)

        assert drag.is_a_resize
        assert drag.resize == pytest.approx(2.0)
        assert drag.refused == ""

    def test_a_resize_arrives_alone(self):
        """Its translation is the scale growing about the part's own centre.

        Reading that column as a Move as well would shift the part by however
        far its centre happens to be from the origin - on a part at x=60,
        doubling it would also fling it 60 mm sideways.
        """
        centre = np.array([60.0, 0.0, 40.0])
        grid = np.eye(4) * 2.0
        grid[3, 3] = 1.0
        grid[:3, 3] = centre - 2.0 * centre
        drag = movement_in(grid)

        assert drag.resize == pytest.approx(2.0)
        assert drag.move is None
        assert drag.turn is None

    def test_shrinking_reads_as_less_than_one(self):
        grid = np.eye(4) * 0.5
        grid[3, 3] = 1.0
        assert movement_in(grid).resize == pytest.approx(0.5)

    def test_a_twitch_of_a_resize_is_not_recorded(self):
        grid = np.eye(4) * 1.001
        grid[3, 3] = 1.0
        assert not movement_in(grid).is_a_resize

    def test_an_uneven_stretch_is_refused(self):
        """Nothing here can scale one axis, so this is corrupt after all."""
        grid = np.eye(4)
        grid[0, 0] = 2.0
        drag = movement_in(grid)

        assert not drag.is_a_resize
        assert "unevenly" in drag.refused

    def test_a_resize_says_so_in_words(self):
        grid = np.eye(4) * 1.5
        grid[3, 3] = 1.0
        assert "150%" in movement_in(grid).describe()

    def test_a_move_still_reads_as_a_move(self):
        """The scale extraction must not disturb the ordinary case."""
        grid = np.eye(4)
        grid[:3, 3] = (5.0, 0.0, 0.0)
        drag = movement_in(grid)

        assert not drag.is_a_resize
        assert drag.move is not None

    def test_a_matrix_of_the_wrong_shape_is_refused(self):
        assert movement_in(np.eye(3)).refused
        assert not movement_in(np.eye(3)).did_anything

    def test_a_matrix_with_nothing_finite_in_it_is_refused(self):
        grid = np.eye(4)
        grid[0, 3] = float("nan")
        assert not movement_in(grid).did_anything

    def test_a_list_of_lists_is_accepted_as_readily_as_an_array(self):
        """VTK hands this over as numbers, not as anything with an opinion."""
        assert movement_in(shifted(5, 0, 0).tolist()).move == Move(5.0, 0.0, 0.0)


class TestWhatIsApplied:
    def test_the_turn_is_applied_before_the_move(self):
        """Both are about the origin, so a moved part would turn about the wrong point."""
        grid = turned(45.0, "Z")
        grid[0:3, 3] = (20.0, 0.0, 0.0)
        commands = movement_in(grid).commands

        assert isinstance(commands[0], Rotate)
        assert isinstance(commands[1], Move)

    def test_a_drag_that_did_nothing_yields_no_commands(self):
        assert movement_in(np.eye(4)).commands == ()

    def test_it_says_what_it_did_in_the_same_words_as_the_tree(self):
        grid = turned(90.0, "Z")
        grid[0:3, 3] = (10.0, 0.0, 0.0)
        told = movement_in(grid).describe()

        assert "Rotate 90 degrees about Z" in told
        assert "Move by (10, 0, 0) mm" in told

    def test_a_drag_that_did_nothing_says_so(self):
        assert "did not move anything" in Drag().describe()

    def test_a_partly_refused_drag_still_reports_the_refusal(self):
        both = turned(30.0, "X") @ turned(30.0, "Y")
        both[0:3, 3] = (10.0, 0.0, 0.0)
        told = movement_in(both).describe()

        assert "Move by" in told
        assert "more than one axis" in told
