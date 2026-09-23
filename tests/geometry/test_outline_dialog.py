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

from modelpop.domain.cad_commands import Loft, Plane, Section, Sweep
from modelpop.ui.outline_dialog import (
    LOFT_PRESETS,
    PRESETS,
    SPIN_PRESETS,
    SWEEP_PRESETS,
    Operation,
    OutlineDialog,
    OutlinePreview,
    describe_outline,
    describe_path,
    describe_profile,
    describe_sections,
    enclosed_area,
    flatten_path,
    parse_outline,
    parse_path,
    parse_sections,
    path_length,
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


class TestReadingAPath:
    """Where a swept outline travels, read out of text."""

    def test_three_numbers_a_line_make_a_point(self):
        assert parse_path("10, 20, 30") == [(10.0, 20.0, 30.0)]

    def test_the_same_separators_work_as_for_an_outline(self):
        assert parse_path("(1; 2; 3)") == parse_path("1 2 3") == [(1.0, 2.0, 3.0)]

    def test_a_two_number_line_is_skipped_rather_than_guessed_at(self):
        """It is an outline corner in the wrong box, not a point missing a height."""
        assert parse_path("1, 2\n3, 4, 5") == [(3.0, 4.0, 5.0)]

    def test_blank_lines_and_comments_are_ignored(self):
        assert parse_path("# path\n\n1, 2, 3\n\n") == [(1.0, 2.0, 3.0)]

    def test_it_measures_how_far_the_path_goes(self):
        assert path_length([(0.0, 0.0, 0.0), (0.0, 0.0, 40.0), (30.0, 0.0, 40.0)]) == 70.0

    def test_a_path_with_one_point_says_it_goes_nowhere(self):
        assert "goes nowhere" in describe_path([(0.0, 0.0, 0.0)])

    def test_a_path_is_described_by_its_length_and_its_corners(self):
        told = describe_path([(0.0, 0.0, 0.0), (0.0, 0.0, 40.0), (30.0, 0.0, 40.0)])
        assert "70 mm long" in told
        assert "1 corner" in told

    def test_a_straight_path_says_it_has_no_corners(self):
        assert "no corners" in describe_path([(0.0, 0.0, 0.0), (0.0, 0.0, 40.0)])


class TestFlatteningAPathToLookAt:
    """A path typed blind is easy to get wrong, so it gets a preview too."""

    def test_a_path_in_the_xz_plane_is_seen_from_the_front(self):
        points, view = flatten_path([(0.0, 0.0, 0.0), (0.0, 0.0, 40.0), (30.0, 0.0, 40.0)])
        assert view == "from the front"
        assert points == [(0.0, 0.0), (0.0, 40.0), (30.0, 40.0)]

    def test_a_path_flat_on_the_bed_is_seen_from_above(self):
        points, view = flatten_path([(0.0, 0.0, 0.0), (30.0, 0.0, 0.0), (30.0, 30.0, 0.0)])
        assert view == "from above"
        assert points == [(0.0, 0.0), (30.0, 0.0), (30.0, 30.0)]

    def test_a_path_in_the_yz_plane_is_seen_from_the_side(self):
        _, view = flatten_path([(0.0, 0.0, 0.0), (0.0, 40.0, 0.0), (0.0, 40.0, 30.0)])
        assert view == "from the side"

    def test_an_empty_path_does_not_fall_over(self):
        """It is flattened on every keystroke, including the first one."""
        assert flatten_path([]) == ([], "from the front")


class TestReadingStackedOutlines:
    """Outlines grouped by the height on each line."""

    STACK = "0, 0, 0\n10, 0, 0\n10, 10, 0\n2, 2, 20\n8, 2, 20\n8, 8, 20"

    def test_lines_sharing_a_height_become_one_outline(self):
        sections = parse_sections(self.STACK)
        assert len(sections) == 2
        assert sections[0] == ([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], 0.0)

    def test_heights_keep_the_order_they_were_typed_in(self):
        """The command sorts them; the text should read back as it was written."""
        assert [height for _, height in parse_sections("1,1,30\n2,2,30\n3,3,0\n4,4,0")] == [
            30.0,
            0.0,
        ]

    def test_lines_sharing_a_height_are_gathered_even_when_separated(self):
        sections = parse_sections("0,0,0\n1,1,20\n10,0,0")
        assert len(sections[0][0]) == 2, "both of the corners at zero"

    def test_one_outline_is_not_a_blend(self):
        assert "at least 2" in describe_sections(parse_sections("0,0,0\n10,0,0\n10,10,0"))

    def test_a_stack_is_described_by_what_it_spans(self):
        told = describe_sections(parse_sections(self.STACK))
        assert "2 outlines over 20 mm" in told
        assert "from 0 to 20 mm" in told

    def test_an_outline_with_too_few_corners_is_named_by_its_height(self):
        told = describe_sections(parse_sections("0,0,0\n10,0,0\n10,10,0\n5,5,25"))
        assert "at 25 mm" in told

    def test_two_outlines_at_one_height_cannot_happen_by_construction(self):
        """Grouping is by height, so the same height is the same outline."""
        assert len(parse_sections("0,0,5\n10,0,5\n10,10,5")) == 1


class TestSweepingAndBlendingInTheDialog:
    """The two operations that need a second drawing."""

    def offering(self, dialog) -> list[str]:
        return [dialog._preset.itemText(i) for i in range(1, dialog._preset.count())]

    def choose(self, dialog, operation: Operation) -> None:
        dialog._operation.setCurrentIndex(list(Operation).index(operation))

    def test_each_operation_offers_its_own_starting_shapes(self, app):
        dialog = OutlineDialog()
        self.choose(dialog, Operation.SWEEP)
        assert self.offering(dialog) == list(SWEEP_PRESETS)

        self.choose(dialog, Operation.LOFT)
        assert self.offering(dialog) == list(LOFT_PRESETS)

    def test_a_sweep_preset_fills_in_both_drawings(self, app):
        """Half a preset leaves the harder half to type from nothing."""
        dialog = OutlineDialog()
        self.choose(dialog, Operation.SWEEP)
        dialog._preset.setCurrentIndex(1 + list(SWEEP_PRESETS).index("Bent tube"))

        section, route = SWEEP_PRESETS["Bent tube"]
        assert dialog.points == section
        assert dialog.path == route

    def test_a_blend_preset_fills_the_corner_box_with_heights(self, app):
        dialog = OutlineDialog()
        self.choose(dialog, Operation.LOFT)
        dialog._preset.setCurrentIndex(1 + list(LOFT_PRESETS).index("Tapered pot"))

        assert dialog.sections == LOFT_PRESETS["Tapered pot"]

    def test_a_sweep_cannot_be_accepted_without_a_path(self, app):
        dialog = OutlineDialog()
        self.choose(dialog, Operation.SWEEP)
        dialog.set_outline(((0, 0), (10, 0), (10, 10), (0, 10)))

        assert not self.ok(dialog).isEnabled(), "an outline with nowhere to go"

        dialog.set_path(((0, 0, 0), (0, 0, 40)))
        assert self.ok(dialog).isEnabled()

    def test_a_blend_cannot_be_accepted_with_one_outline(self, app):
        dialog = OutlineDialog()
        self.choose(dialog, Operation.LOFT)
        dialog.set_sections(((((0, 0), (10, 0), (10, 10)), 0.0),))

        assert not self.ok(dialog).isEnabled()

        dialog.set_sections(((((0, 0), (10, 0), (10, 10)), 0.0), (((2, 2), (8, 2), (8, 8)), 20.0)))
        assert self.ok(dialog).isEnabled()

    def test_a_blend_whose_outlines_enclose_nothing_cannot_be_accepted(self, app):
        dialog = OutlineDialog()
        self.choose(dialog, Operation.LOFT)
        dialog.set_sections(((((0, 0), (10, 0), (20, 0)), 0.0), (((2, 2), (8, 2), (8, 8)), 20.0)))

        assert not self.ok(dialog).isEnabled(), "the lower outline is a straight line"

    def test_the_bend_has_a_sensible_default(self, app):
        dialog = OutlineDialog()
        self.choose(dialog, Operation.SWEEP)
        assert dialog.bend_radius > 0, "a mitred corner is not a shape"

    def test_the_hint_says_what_the_numbers_mean_for_each_operation(self, app):
        dialog = OutlineDialog()
        said: dict[Operation, str] = {}
        for operation in Operation:
            self.choose(dialog, operation)
            said[operation] = dialog._hint.text()

        assert len(set(said.values())) == len(Operation), "two operations share a hint"
        assert "cross-section" in said[Operation.SWEEP]
        assert "height its outline sits at" in said[Operation.LOFT]

    def test_the_path_rows_are_only_there_when_sweeping(self, app):
        dialog = OutlineDialog()
        self.choose(dialog, Operation.SWEEP)
        assert dialog._path.isVisibleTo(dialog)

        self.choose(dialog, Operation.LOFT)
        assert not dialog._path.isVisibleTo(dialog)

    @pytest.mark.parametrize("name", list(SWEEP_PRESETS))
    def test_every_sweep_preset_is_a_shape_that_goes_somewhere(self, name):
        section, route = SWEEP_PRESETS[name]
        assert enclosed_area(list(section)) > 1.0
        assert len(route) >= 2
        assert path_length(list(route)) > 0

    @pytest.mark.parametrize("name", list(SWEEP_PRESETS))
    def test_every_sweep_preset_builds_a_command_with_no_complaint(self, name):
        section, route = SWEEP_PRESETS[name]
        assert Sweep(section, route, 3.0).problem is None

    @pytest.mark.parametrize("name", list(LOFT_PRESETS))
    def test_every_blend_preset_builds_a_command_with_no_complaint(self, name):
        sections = tuple(Section(points, height) for points, height in LOFT_PRESETS[name])
        assert Loft(sections).problem is None

    def test_the_preview_shows_every_outline_in_a_blend(self, app):
        preview = OutlinePreview()
        preview.show_outlines([[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], [(2.0, 2.0)]])
        preview.show_outlines([])

    def test_the_preview_survives_a_path_with_nothing_in_it(self, app):
        """It is redrawn on every keystroke, including half-typed ones."""
        preview = OutlinePreview()
        preview.show_closed(False)
        for corners in ([], [(0.0, 0.0)], [(1.0, 1.0), (1.0, 1.0)]):
            preview.show_outline(corners)

    def ok(self, dialog):
        return dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
