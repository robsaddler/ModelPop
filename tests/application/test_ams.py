"""One plate with an AMS, or one plate per colour.

Driven by synthetic toolpaths, because the point is the arithmetic and the
honesty of it - what is measured, what is a guess, and what the app refuses to
claim when the file did not say.
"""

import textwrap

import pytest

from modelpop.printing.ams import (
    PLATE_CHANGE_SECONDS,
    PrintStrategy,
    compare_strategies,
)
from modelpop.printing.toolpath import Toolpath

FLUSH = "; flush_volumes_matrix = 0,280,280,280,280,0,280,280,280,280,0,280,280,280,280,0\n"
HEADER = "M83\nG90\n"


def toolpath(minutes: float, changes: int = 0, *, flush: bool = True) -> Toolpath:
    """A file that states a time, and swaps filament a given number of times."""
    body = f"; total estimated time: {minutes:.0f}m 0s\n"
    if flush:
        body += FLUSH
    body += HEADER
    for index in range(changes):
        body += f"T{(index % 3) + 1}\n"
    body += textwrap.dedent("""
        ; CHANGE_LAYER
        ; Z_HEIGHT: 0.2
        G1 X100 Y100 F9000
        G1 X120 Y100 E1.0
    """)
    return Toolpath.parse(body.splitlines())


class TestWhatEachStrategyCosts:
    def test_the_single_plate_needs_nobody(self):
        """The whole reason an AMS is worth anything."""
        comparison = compare_strategies(toolpath(60, changes=4), [toolpath(20), toolpath(20)])
        assert comparison.single_plate.interventions == 0

    def test_the_first_plate_does_not_need_a_change(self):
        """Three colours means two plate changes, not three."""
        comparison = compare_strategies(
            toolpath(60, changes=4), [toolpath(20), toolpath(20), toolpath(20)]
        )
        assert comparison.multi_plate.interventions == 2

    def test_a_single_colour_needs_no_changes_at_all(self):
        comparison = compare_strategies(toolpath(60), [toolpath(60)])
        assert comparison.multi_plate.interventions == 0

    def test_purge_is_counted_per_change_from_the_slicers_own_matrix(self):
        """280 mm3 per swap on this profile, stated by the slicer."""
        comparison = compare_strategies(toolpath(60, changes=4), [toolpath(30), toolpath(30)])
        assert comparison.single_plate.waste_mm3 == pytest.approx(4 * 280)

    def test_the_purge_is_weighed_rather_than_left_as_a_volume(self):
        """Grams is what a spool is sold in, and what the user thinks in."""
        comparison = compare_strategies(toolpath(60, changes=10), [toolpath(30), toolpath(30)])
        assert comparison.single_plate.waste_grams() == pytest.approx(2800 / 1000 * 1.24, rel=0.01)

    def test_separate_plates_waste_nothing(self):
        comparison = compare_strategies(toolpath(60, changes=4), [toolpath(30), toolpath(30)])
        assert comparison.multi_plate.waste_mm3 == 0

    def test_the_printing_time_is_the_slicers_own_estimate(self):
        comparison = compare_strategies(toolpath(60, changes=4), [toolpath(25), toolpath(20)])
        assert comparison.single_plate.print_seconds == pytest.approx(3600)
        assert comparison.multi_plate.print_seconds == pytest.approx(45 * 60)

    def test_a_persons_time_is_added_to_the_plate_changes(self):
        comparison = compare_strategies(toolpath(60, changes=4), [toolpath(25), toolpath(20)])
        expected = 45 * 60 + PLATE_CHANGE_SECONDS
        assert comparison.multi_plate.total_seconds() == pytest.approx(expected)


class TestTheTrade:
    def test_a_model_that_swaps_rarely_favours_the_ams(self):
        """Two swaps of purge against half an hour of a person's evening."""
        comparison = compare_strategies(toolpath(60, changes=2), [toolpath(30), toolpath(30)])
        assert comparison.seconds_saved > 0
        assert "Use the AMS" in comparison.recommendation()

    def test_a_model_that_swaps_constantly_favours_separate_plates(self):
        """Hundreds of swaps is more filament in the bin than in the model, and
        here it is slower into the bargain."""
        comparison = compare_strategies(toolpath(70, changes=400), [toolpath(30), toolpath(30)])
        assert comparison.grams_wasted > 100
        assert "One plate per colour" in comparison.recommendation()

    def test_a_wasteful_but_faster_single_plate_is_judged_on_the_exchange_rate(self):
        comparison = compare_strategies(
            toolpath(60, changes=100), [toolpath(30), toolpath(30), toolpath(30)]
        )
        assert comparison.grams_per_hour_saved > 40
        assert "poor trade" in comparison.recommendation()

    def test_a_single_plate_that_is_also_slower_has_nothing_to_offer(self):
        comparison = compare_strategies(toolpath(200, changes=10), [toolpath(30), toolpath(30)])
        assert comparison.seconds_saved < 0
        assert "nothing to trade" in comparison.recommendation()

    def test_no_colour_changes_means_there_is_no_question(self):
        comparison = compare_strategies(toolpath(60), [toolpath(60)])
        assert "no colour changes" in comparison.recommendation()

    def test_the_exchange_rate_is_the_number_the_decision_turns_on(self):
        comparison = compare_strategies(toolpath(60, changes=20), [toolpath(40), toolpath(40)])
        assert comparison.grams_per_hour_saved > 0

    def test_grams_per_hour_is_zero_when_no_hours_were_saved(self):
        """It would otherwise be a negative number pretending to mean something."""
        comparison = compare_strategies(toolpath(300, changes=10), [toolpath(30), toolpath(30)])
        assert comparison.grams_per_hour_saved == 0

    def test_a_longer_plate_change_shifts_the_answer_towards_the_ams(self):
        """Whoever has to stand there decides this, so it is a parameter."""
        quick = compare_strategies(
            toolpath(60, changes=6), [toolpath(30), toolpath(30)], plate_change_seconds=30
        )
        slow = compare_strategies(
            toolpath(60, changes=6), [toolpath(30), toolpath(30)], plate_change_seconds=1800
        )
        assert slow.seconds_saved > quick.seconds_saved


class TestWhatItRefusesToClaim:
    def test_a_file_with_no_flush_matrix_says_the_waste_is_unknown(self):
        """Not zero. A multi-colour print reported as wasting nothing would be
        a confident wrong answer, which is worse than no answer."""
        comparison = compare_strategies(
            toolpath(60, changes=4, flush=False), [toolpath(30), toolpath(30)]
        )
        assert not comparison.flush_was_stated
        assert "cannot be worked out" in comparison.recommendation()

    def test_a_single_colour_file_with_no_matrix_is_not_treated_as_unknown(self):
        """There are no changes, so there is nothing to purge and nothing to
        be uncertain about."""
        comparison = compare_strategies(toolpath(60, flush=False), [toolpath(60)])
        assert comparison.flush_was_stated

    def test_the_table_admits_which_number_is_a_guess(self):
        comparison = compare_strategies(toolpath(60, changes=4), [toolpath(30), toolpath(30)])
        table = comparison.table()

        assert "estimate, not a measurement" in table

    def test_the_table_does_not_mention_a_guess_that_does_not_apply(self):
        """One plate, one colour: nobody changes anything."""
        comparison = compare_strategies(toolpath(60), [toolpath(60)])
        assert "estimate, not a measurement" not in comparison.table()

    def test_both_options_are_described_in_words(self):
        comparison = compare_strategies(toolpath(60, changes=4), [toolpath(30), toolpath(30)])
        table = comparison.table()

        assert "One plate, AMS" in table
        assert "One plate per colour" in table
        assert "unattended" in table


class TestDescribingOneStrategy:
    def test_a_strategy_with_no_waste_says_so_plainly(self):
        strategy = PrintStrategy(
            "x", plates=2, print_seconds=600, waste_mm3=0, changes=0, interventions=1
        )
        assert "nothing wasted" in strategy.describe()

    def test_a_long_print_is_described_in_hours(self):
        strategy = PrintStrategy(
            "x", plates=1, print_seconds=9000, waste_mm3=0, changes=0, interventions=0
        )
        assert "2h 30m" in strategy.describe()


class TestWhenTheWasteIsNegligible:
    """A spool is a kilogram. A rate in grams per hour is noise when the
    numerator is a fraction of a gram."""

    def test_two_swaps_is_not_a_decision_anybody_needs_to_weigh(self):
        comparison = compare_strategies(toolpath(60, changes=2), [toolpath(30), toolpath(30)])
        recommendation = comparison.recommendation()

        assert comparison.grams_wasted < 1
        assert "Use the AMS" in recommendation
        assert "nothing off a spool" in recommendation

    def test_enough_waste_to_matter_is_still_weighed_properly(self):
        """One plate is genuinely faster here - three separate prints and two
        plate changes - but it buys that speed with a third of a spool."""
        comparison = compare_strategies(
            toolpath(60, changes=100), [toolpath(30), toolpath(30), toolpath(30)]
        )
        assert comparison.seconds_saved > 0
        assert comparison.grams_wasted > 5
        assert "poor trade" in comparison.recommendation()

    def test_the_wasted_amount_is_shown_to_a_tenth_of_a_gram(self):
        """Rounding a gram to zero would make the message read as a bug."""
        comparison = compare_strategies(toolpath(60, changes=2), [toolpath(30), toolpath(30)])
        assert "0.7 g" in comparison.recommendation()
