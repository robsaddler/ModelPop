"""Cutting the view open, driven with no display and no VTK.

The section is the answer to the one question a hollow leaves hanging - did
that wall come out at the thickness I asked for - so the rules that matter are
about the cut always landing somewhere you can see something. A plane outside
the part shows either the whole model or none of it, and both read as the tool
being broken rather than as the plane being in the wrong place.
"""

import pytest

from modelpop.domain.mesh import BoundingBox
from modelpop.presentation.sectioning import Axis, SectionPlane, SectionTool

CUBE = BoundingBox(-20.0, -20.0, 0.0, 20.0, 20.0, 40.0)
TALL = BoundingBox(-5.0, -5.0, 0.0, 5.0, 5.0, 150.0)


def tool(bounds: BoundingBox | None = CUBE) -> SectionTool:
    cutter = SectionTool()
    cutter.fits(bounds)
    return cutter


class TestSwitchingItOn:
    def test_it_starts_off(self):
        assert not tool().is_on
        assert tool().plane is None

    def test_turning_it_on_cuts_through_the_middle(self):
        """The middle of the model, not the middle of the build volume."""
        cutter = tool()
        cutter.turn_on()

        assert cutter.is_on
        assert cutter.offset == pytest.approx(0.0), "the cube straddles the origin in X"

    def test_it_cuts_through_the_middle_of_a_model_that_is_not_centred(self):
        cutter = tool(BoundingBox(10.0, 0.0, 0.0, 50.0, 20.0, 20.0))
        cutter.turn_on()

        assert cutter.offset == pytest.approx(30.0)

    def test_turning_it_off_leaves_nothing_to_cut_with(self):
        cutter = tool()
        cutter.turn_on()
        cutter.turn_off()

        assert cutter.plane is None

    def test_it_toggles(self):
        cutter = tool()
        assert cutter.toggle() is True
        assert cutter.toggle() is False

    def test_with_nothing_on_screen_it_says_so(self):
        cutter = tool(None)
        cutter.turn_on()

        assert not cutter.has_something_to_cut
        assert "nothing on the plate" in cutter.describe()


class TestWhereTheCutGoes:
    def test_the_cut_stays_inside_the_model(self):
        """Outside it, the whole part is shown or none of it is."""
        cutter = tool()
        cutter.turn_on()
        cutter.move_to(500.0)

        assert cutter.offset < 20.0
        cutter.move_to(-500.0)
        assert cutter.offset > -20.0

    def test_it_never_sits_exactly_on_a_face(self):
        low, high = tool().travel
        assert low > -20.0
        assert high < 20.0

    def test_the_travel_follows_the_axis_it_is_cutting_along(self):
        cutter = tool(TALL)
        cutter.turn_on()
        across = cutter.travel

        cutter.cut_along(Axis.Z)
        up = cutter.travel

        assert up[1] - up[0] > across[1] - across[0], "the tall axis has more travel"

    def test_changing_axis_moves_the_cut_back_to_the_middle(self):
        """Carried over, it lands outside a part that is long one way and short the other."""
        cutter = tool(TALL)
        cutter.cut_along(Axis.Z)
        cutter.move_to(140.0)
        cutter.cut_along(Axis.X)

        assert cutter.offset == pytest.approx(0.0)
        assert -5.0 < cutter.offset < 5.0

    def test_a_model_too_thin_to_cut_gives_no_travel_rather_than_a_backwards_range(self):
        """A 0.2 mm sheet has no room for a margin at each end."""
        low, high = tool(BoundingBox(0.0, 0.0, 0.0, 0.2, 40.0, 40.0)).travel
        assert low <= high

    def test_a_new_model_pulls_the_cut_back_inside_it(self):
        cutter = tool(TALL)
        cutter.cut_along(Axis.Z)
        cutter.move_to(145.0)

        cutter.fits(CUBE)

        assert cutter.offset <= 40.0, "the cut was stranded above the new part"

    def test_with_no_model_the_cut_falls_back_to_the_origin(self):
        cutter = tool()
        cutter.move_to(15.0)
        cutter.fits(None)

        assert cutter.offset == pytest.approx(0.0)


class TestWhichHalfIsKept:
    def test_flipping_reverses_the_normal(self):
        cutter = tool()
        cutter.turn_on()
        before = cutter.plane.normal
        cutter.flip()

        assert cutter.plane.normal == tuple(-component for component in before)

    def test_flipping_leaves_the_cut_where_it_is(self):
        cutter = tool()
        cutter.turn_on()
        cutter.move_to(12.0)
        cutter.flip()

        assert cutter.offset == pytest.approx(12.0)

    def test_flipping_survives_a_change_of_axis(self):
        cutter = tool()
        cutter.flip()
        cutter.cut_along(Axis.Y)

        assert cutter.flipped


class TestThePlaneItself:
    def test_the_origin_sits_on_the_axis(self):
        assert SectionPlane(Axis.Z, 15.0).origin == (0.0, 0.0, 15.0)
        assert SectionPlane(Axis.X, -4.0).origin == (-4.0, 0.0, 0.0)

    @pytest.mark.parametrize("axis", list(Axis))
    def test_every_axis_has_a_unit_normal(self, axis):
        assert sum(abs(component) for component in SectionPlane(axis).normal) == 1.0

    def test_every_axis_reads_as_english(self):
        assert {axis.describe for axis in Axis} == {
            "left to right",
            "front to back",
            "top to bottom",
        }


class TestWhatTheUserIsTold:
    def test_it_says_when_it_is_off(self):
        assert "not cut open" in tool().describe()

    def test_it_says_where_the_cut_is_and_which_half_is_kept(self):
        cutter = tool()
        cutter.turn_on()
        cutter.cut_along(Axis.Z)
        cutter.move_to(12.0)

        told = cutter.describe()
        assert "top to bottom" in told
        assert "12.0 mm" in told
        assert "near half" in told

        cutter.flip()
        assert "far half" in cutter.describe()
