"""Where a part lands, worked out without a viewport anywhere near it.

These are the two placements that were previously done by eye with a drag
handle. Doing them by eye is how a sphere ends up welded halfway through the
plate, which is exactly what happened.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from modelpop.domain.mesh import BoundingBox
from modelpop.domain.placement import centre_over_bed, nudge, settle_onto_bed


def box(
    min_x: float = -10.0,
    min_y: float = -10.0,
    min_z: float = 0.0,
    max_x: float = 10.0,
    max_y: float = 10.0,
    max_z: float = 20.0,
) -> BoundingBox:
    return BoundingBox(min_x, min_y, min_z, max_x, max_y, max_z)


class TestSettlingOntoTheBed:
    def test_a_part_sunk_through_the_plate_is_lifted(self):
        """The bug the user actually saw, as a number."""
        move = settle_onto_bed(box(min_z=-20.0, max_z=20.0))
        assert move.dz == pytest.approx(20.0)

    def test_a_part_floating_above_the_plate_is_dropped(self):
        move = settle_onto_bed(box(min_z=15.0, max_z=35.0))
        assert move.dz == pytest.approx(-15.0)

    def test_a_part_already_seated_is_not_moved_at_all(self):
        """So the panel can say so rather than adding a pointless step."""
        move = settle_onto_bed(box(min_z=0.0))
        assert (move.dx, move.dy, move.dz) == (0.0, 0.0, 0.0)

    def test_it_leaves_the_plan_position_alone(self):
        move = settle_onto_bed(box(min_x=30.0, max_x=50.0, min_z=-5.0))
        assert move.dx == 0.0
        assert move.dy == 0.0

    @given(bottom=st.floats(min_value=-500.0, max_value=500.0, allow_nan=False))
    def test_the_bottom_always_ends_up_on_zero(self, bottom: float):
        """The property, rather than three examples of it."""
        move = settle_onto_bed(box(min_z=bottom, max_z=bottom + 10.0))
        assert bottom + move.dz == pytest.approx(0.0, abs=1e-6)


class TestCentringOverThePlate:
    def test_it_slides_the_middle_of_the_part_over_the_middle_of_the_plate(self):
        move = centre_over_bed(box(min_x=10.0, max_x=30.0, min_y=-40.0, max_y=-20.0))
        assert move.dx == pytest.approx(-20.0)
        assert move.dy == pytest.approx(30.0)

    def test_it_never_changes_the_height(self):
        """Centring and settling are separate steps, so neither undoes the other."""
        move = centre_over_bed(box(min_z=-17.0, max_z=3.0))
        assert move.dz == 0.0

    def test_a_part_already_centred_is_not_moved(self):
        assert centre_over_bed(box()).dx == 0.0
        assert centre_over_bed(box()).dy == 0.0

    @given(
        centre_x=st.floats(min_value=-200.0, max_value=200.0, allow_nan=False),
        centre_y=st.floats(min_value=-200.0, max_value=200.0, allow_nan=False),
    )
    def test_the_centre_always_ends_up_on_the_origin(self, centre_x: float, centre_y: float):
        move = centre_over_bed(
            box(
                min_x=centre_x - 5.0,
                max_x=centre_x + 5.0,
                min_y=centre_y - 5.0,
                max_y=centre_y + 5.0,
            )
        )
        assert centre_x + move.dx == pytest.approx(0.0, abs=1e-6)
        assert centre_y + move.dy == pytest.approx(0.0, abs=1e-6)


class TestNudging:
    def test_each_axis_moves_only_itself(self):
        assert (nudge("X", 5.0).dx, nudge("X", 5.0).dy, nudge("X", 5.0).dz) == (5.0, 0.0, 0.0)
        assert (nudge("Y", 5.0).dx, nudge("Y", 5.0).dy, nudge("Y", 5.0).dz) == (0.0, 5.0, 0.0)
        assert (nudge("Z", 5.0).dx, nudge("Z", 5.0).dy, nudge("Z", 5.0).dz) == (0.0, 0.0, 5.0)

    def test_it_goes_backwards_too(self):
        assert nudge("Y", -2.5).dy == pytest.approx(-2.5)

    def test_the_axis_name_is_not_case_sensitive(self):
        assert nudge("x", 1.0).dx == 1.0

    def test_an_axis_it_does_not_know_is_taken_as_upright(self):
        """Matching what ``Rotate`` already does, rather than raising."""
        assert nudge("sideways", 1.0).dz == 1.0
