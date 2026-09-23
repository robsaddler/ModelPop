"""Reading an outline out of text, and what the user is told about it.

The parsing is the part that matters. Corners arrive typed, pasted from a
spreadsheet, or copied out of a chat window, and every one of those formats
looks different. Getting it wrong is silent: a skipped line makes a different
shape, and the shape still builds.

The dialog itself is built here too, with the offscreen platform ``conftest``
sets, because the enabling rule - a shape that encloses nothing cannot be
accepted - is the one piece of behaviour in it.
"""

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from modelpop.domain.cad_commands import Plane
from modelpop.ui.outline_dialog import (
    PRESETS,
    SPIN_PRESETS,
    Operation,
    OutlineDialog,
    OutlinePreview,
    describe_outline,
    describe_profile,
    enclosed_area,
    parse_outline,
)

SQUARE = "0, 0\n10, 0\n10, 10\n0, 10"


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


class TestReadingAnOutline:
    """Every shape of text somebody might reasonably paste in."""

    def test_commas_and_spaces_mean_the_same_thing(self):
        assert parse_outline("10, 20") == parse_outline("10 20") == [(10.0, 20.0)]

    def test_brackets_are_ignored(self):
        assert parse_outline("(10, 20)") == [(10.0, 20.0)]
        assert parse_outline("[10; 20]") == [(10.0, 20.0)]

    def test_a_whole_outline_comes_back_in_order(self):
        assert parse_outline(SQUARE) == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]

    def test_blank_lines_and_comments_are_skipped(self):
        assert parse_outline("# corners\n\n1, 2\n\n3, 4\n") == [(1.0, 2.0), (3.0, 4.0)]

    def test_one_bad_line_does_not_lose_the_rest(self):
        """Half an outline is more useful than none while it is being typed."""
        assert parse_outline("1, 2\nnonsense\n3, 4") == [(1.0, 2.0), (3.0, 4.0)]

    def test_a_line_with_three_numbers_is_not_a_corner(self):
        assert parse_outline("1, 2, 3") == []

    def test_negatives_and_decimals_survive(self):
        assert parse_outline("-1.5, 2.25") == [(-1.5, 2.25)]

    def test_nothing_in_means_nothing_out(self):
        assert parse_outline("") == []


class TestMeasuringAnOutline:
    """What the user is told before they commit to a shape."""

    def test_a_square_has_the_area_of_a_square(self):
        assert enclosed_area(parse_outline(SQUARE)) == pytest.approx(100.0)

    def test_winding_direction_does_not_change_the_area(self):
        corners = parse_outline(SQUARE)
        assert enclosed_area(corners) == pytest.approx(enclosed_area(list(reversed(corners))))

    def test_two_corners_enclose_nothing(self):
        assert enclosed_area([(0.0, 0.0), (10.0, 0.0)]) == 0.0

    def test_corners_in_a_straight_line_enclose_nothing(self):
        assert enclosed_area([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]) == pytest.approx(0.0)

    def test_too_few_corners_says_how_many_are_needed(self):
        assert "at least 3" in describe_outline([(0.0, 0.0)])

    def test_a_flat_outline_is_called_out_rather_than_measured(self):
        told = describe_outline([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])
        assert "no area" in told

    def test_a_real_outline_is_described_in_the_units_on_the_ruler(self):
        told = describe_outline(parse_outline(SQUARE))
        assert "4 corners" in told
        assert "10 by 10 mm" in told
        assert "1.0 sq cm" in told

    @pytest.mark.parametrize("name", list(PRESETS))
    def test_every_preset_is_a_shape_that_encloses_something(self, name):
        assert enclosed_area(list(PRESETS[name])) > 1.0


class TestTheDialog:
    """The few rules the dialog owns rather than reads off a function."""

    def test_it_starts_empty_and_refusing(self, app):
        dialog = OutlineDialog()
        assert dialog.points == ()
        assert (
            not dialog.findChild(QDialogButtonBox)
            .button(QDialogButtonBox.StandardButton.Ok)
            .isEnabled()
        )

    def test_an_outline_that_encloses_something_can_be_accepted(self, app):
        dialog = OutlineDialog()
        dialog.set_outline(((0, 0), (10, 0), (10, 10), (0, 10)))

        assert dialog.points == ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        assert (
            dialog.findChild(QDialogButtonBox)
            .button(QDialogButtonBox.StandardButton.Ok)
            .isEnabled()
        )

    def test_a_line_cannot_be_accepted(self, app):
        dialog = OutlineDialog()
        dialog.set_outline(((0, 0), (10, 0)))

        assert (
            not dialog.findChild(QDialogButtonBox)
            .button(QDialogButtonBox.StandardButton.Ok)
            .isEnabled()
        )

    def test_it_answers_with_what_was_chosen(self, app):
        dialog = OutlineDialog()
        dialog.set_outline(PRESETS["L-bracket"])

        assert dialog.thickness == pytest.approx(8.0), "a sensible default plate"
        assert dialog.plane is Plane.XY
        assert not dialog.cut

    def test_the_preview_survives_being_handed_a_degenerate_outline(self, app):
        """It is redrawn on every keystroke, including half-typed ones."""
        preview = OutlinePreview()
        for corners in ([], [(0.0, 0.0)], [(1.0, 1.0), (1.0, 1.0)]):
            preview.show_outline(corners)


class TestSpinningInstead:
    """The same dialog, asked for a solid of revolution.

    One dialog for both operations, so the thing worth testing is that it says
    the right thing for whichever is chosen - a profile described as a flat
    outline when it is about to be spun would mislead about the finished size.
    """

    CUP = ((0.0, 0.0), (15.0, 0.0), (15.0, 50.0), (12.0, 50.0), (12.0, 3.0), (0.0, 3.0))

    def test_a_spun_profile_is_described_by_what_it_becomes(self):
        told = describe_profile(list(self.CUP))
        assert "30 mm across" in told, "twice the widest radius"
        assert "50 mm tall" in told

    def test_a_spun_profile_that_encloses_nothing_says_so(self):
        assert "at least 3" in describe_profile([(0.0, 0.0)])
        assert "no area" in describe_profile([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)])

    @pytest.mark.parametrize("name", list(SPIN_PRESETS))
    def test_every_spin_preset_encloses_something(self, name):
        assert enclosed_area(list(SPIN_PRESETS[name])) > 1.0

    @pytest.mark.parametrize("name", list(SPIN_PRESETS))
    def test_no_spin_preset_strays_inside_the_axis(self, name):
        """A negative radius sweeps the same material twice."""
        assert min(radius for radius, _ in SPIN_PRESETS[name]) >= 0

    def test_choosing_to_spin_changes_the_presets_on_offer(self, app):
        dialog = OutlineDialog()
        started_as = dialog.operation
        offered_first = [dialog._preset.itemText(i) for i in range(1, dialog._preset.count())]

        dialog._operation.setCurrentIndex(list(Operation).index(Operation.REVOLVE))
        offered_now = [dialog._preset.itemText(i) for i in range(1, dialog._preset.count())]

        assert started_as is Operation.EXTRUDE, "the commoner of the two"
        assert offered_first == list(PRESETS)
        assert offered_now == list(SPIN_PRESETS)

    def test_a_spin_preset_can_be_chosen_and_read_back(self, app):
        dialog = OutlineDialog()
        dialog._operation.setCurrentIndex(list(Operation).index(Operation.REVOLVE))
        dialog.set_outline(SPIN_PRESETS["Washer"])

        assert dialog.points == SPIN_PRESETS["Washer"]
        assert dialog.degrees == pytest.approx(360.0), "a full turn by default"

    def test_the_summary_follows_the_operation(self, app):
        dialog = OutlineDialog()
        dialog.set_outline(self.CUP)
        flat = dialog._summary.text()

        dialog._operation.setCurrentIndex(list(Operation).index(Operation.REVOLVE))
        dialog.set_outline(self.CUP)

        assert "sq cm" in flat, "an outline is described by the area it encloses"
        assert "30 mm across" in dialog._summary.text()
