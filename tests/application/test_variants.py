"""Several answers to the same question, and going back to an earlier one.

A generative model asked twice gives two different shapes, and the first is
rarely the best. All of this - generate three, look at the second, go back to
the first - runs here with no display, no GPU and no generator.
"""

import numpy as np
import pytest

from modelpop.domain.mesh import Mesh
from modelpop.presentation.variants import KEEP_AT_MOST, GenerationHistory, Variant


def box(size: float = 20.0, seed: int = 0) -> Mesh:
    half = size / 2
    vertices = np.array(
        [
            [-half, -half, -half],
            [half, -half, -half],
            [half, half, -half],
            [-half, half, -half],
            [-half, -half, half],
            [half, -half, half],
            [half, half, half],
            [-half, half, half],
        ],
        dtype=np.float64,
    )
    faces = np.array(
        [
            [0, 3, 2],
            [0, 2, 1],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [2, 3, 7],
            [2, 7, 6],
            [1, 2, 6],
            [1, 6, 5],
            [0, 4, 7],
            [0, 7, 3],
        ],
        dtype=np.int32,
    )
    return Mesh(vertices, faces)


def variant(label: str = "", seed: int = 0, size: float = 20.0) -> Variant:
    return Variant(mesh=box(size), provenance="Generated from a picture", label=label, seed=seed)


class TestHoldingThem:
    def test_it_starts_with_nothing(self):
        history = GenerationHistory()
        assert history.is_empty
        assert history.chosen is None
        assert "Nothing generated yet" in history.describe()

    def test_the_newest_is_first_because_that_is_how_it_is_shown(self):
        history = GenerationHistory()
        history.add(variant("first"))
        history.add(variant("second"))

        assert [v.label for v in history] == ["second", "first"]

    def test_something_just_made_is_what_you_are_looking_at(self):
        """The list must agree with the viewport rather than need a click."""
        history = GenerationHistory()
        history.add(variant("only"))

        assert history.chosen is not None
        assert history.chosen.label == "only"
        assert history.chosen_index == 0

    def test_a_batch_arrives_together_and_selects_its_first(self):
        history = GenerationHistory()
        history.add_all([variant("one"), variant("two"), variant("three")])

        assert [v.label for v in history] == ["one", "two", "three"]
        assert history.chosen is not None
        assert history.chosen.label == "one"

    def test_an_empty_batch_changes_nothing(self):
        history = GenerationHistory()
        history.add(variant("kept"))
        history.add_all([])

        assert len(history) == 1
        assert history.chosen is not None
        assert history.chosen.label == "kept"

    def test_a_batch_after_an_earlier_one_goes_in_front_of_it(self):
        history = GenerationHistory()
        history.add(variant("old"))
        history.add_all([variant("new one"), variant("new two")])

        assert [v.label for v in history] == ["new one", "new two", "old"]


class TestNotGrowingForever:
    def test_the_oldest_goes_once_it_is_full(self):
        """These are meshes of a few megabytes each, held in memory."""
        history = GenerationHistory(keep=3)
        for index in range(5):
            history.add(variant(f"v{index}"))

        assert len(history) == 3
        assert [v.label for v in history] == ["v4", "v3", "v2"]

    def test_the_default_cap_is_more_than_anybody_compares_at_once(self):
        assert KEEP_AT_MOST >= 5

    def test_a_cap_of_nothing_still_keeps_one(self):
        """Otherwise adding a shape and finding none is a very strange result."""
        history = GenerationHistory(keep=0)
        history.add(variant("only"))

        assert len(history) == 1

    def test_it_says_when_it_has_started_dropping_things(self):
        history = GenerationHistory(keep=2)
        history.add(variant())
        history.add(variant())

        assert "oldest are dropped" in history.describe()

    def test_it_does_not_say_so_before_it_is_full(self):
        history = GenerationHistory(keep=5)
        history.add(variant())

        assert "oldest" not in history.describe()


class TestGoingBack:
    def test_an_earlier_one_can_be_chosen_again(self):
        history = GenerationHistory()
        history.add_all([variant("one"), variant("two"), variant("three")])

        chosen = history.choose(2)

        assert chosen is not None
        assert chosen.label == "three"
        assert history.chosen is not None
        assert history.chosen.label == "three"

    def test_a_stale_click_is_ignored_rather_than_raising(self):
        """A list that has since been trimmed is an ordinary thing."""
        history = GenerationHistory(keep=2)
        history.add_all([variant("one"), variant("two")])

        assert history.choose(9) is None
        assert history.choose(-1) is None

    def test_a_stale_click_leaves_the_selection_alone(self):
        history = GenerationHistory()
        history.add_all([variant("one"), variant("two")])
        history.choose(1)
        history.choose(99)

        assert history.chosen is not None
        assert history.chosen.label == "two"

    def test_choosing_from_an_empty_history_answers_nothing(self):
        assert GenerationHistory().choose(0) is None

    def test_clearing_forgets_everything(self):
        history = GenerationHistory()
        history.add_all([variant("one"), variant("two")])
        history.clear()

        assert history.is_empty
        assert history.chosen is None
        assert history.chosen_index is None


class TestWhatEachOneSays:
    def test_a_label_is_used_when_there_is_one(self):
        assert variant("Variant 2").title == "Variant 2"

    def test_without_a_label_it_falls_back_to_the_facts(self):
        assert "triangles" in variant().title

    def test_the_facts_are_the_ones_worth_comparing_two_candidates_on(self):
        told = variant(size=40.0).subtitle
        assert "triangles" in told
        assert "40" in told

    def test_the_seed_is_kept_because_a_shape_you_cannot_reproduce_is_lost(self):
        assert "seed 1234" in variant(seed=1234).subtitle

    def test_no_seed_is_not_reported_as_seed_zero(self):
        """Zero means "pick one", so saying it would be a lie about the run."""
        assert "seed" not in variant(seed=0).subtitle

    def test_the_whole_description_includes_where_it_came_from(self):
        assert "Generated from a picture" in variant("Variant 1").describe()

    def test_two_variants_made_at_once_are_still_distinguishable(self):
        """Their timestamps may be identical, so the label has to carry it."""
        one, two = variant("Variant 1", seed=1), variant("Variant 2", seed=2)
        assert one.title != two.title

    def test_it_counts_one_shape_in_the_singular(self):
        history = GenerationHistory()
        history.add(variant())
        assert "1 shape made" in history.describe()

    def test_it_counts_several_in_the_plural(self):
        history = GenerationHistory()
        history.add_all([variant(), variant()])
        assert "2 shapes made" in history.describe()

    def test_a_variant_records_when_it_was_made(self):
        assert variant().made_at is not None

    @pytest.mark.parametrize("count", [1, 3, KEEP_AT_MOST])
    def test_any_number_of_them_describes_without_raising(self, count):
        history = GenerationHistory()
        history.add_all([variant(f"v{i}") for i in range(count)])
        assert history.describe()
