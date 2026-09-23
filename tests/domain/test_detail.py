"""Whether a detail bake would show, decided before anything is touched.

Arithmetic on four numbers, which is the point: "would this change the printed
object or only the mesh" has an answer without a vertex being moved. The floors
here were measured, not looked up - see docs/research/spike-detail-rescue.md.
"""

import pytest

from modelpop.domain.detail import MAX_SUBDIVISIONS, plan_detail
from modelpop.domain.printer import Nozzle


class TestWhetherItWouldShowAtAll:
    def test_a_bump_shallower_than_a_layer_is_deepened_rather_than_wasted(self):
        """Below one layer the mesh changes and the G-code does not."""
        plan = plan_detail(0.05, 50_000, 60.0, layer_height_mm=0.2)

        assert plan.amplitude_mm == pytest.approx(0.2)
        assert plan.was_clamped
        assert "shallower than one layer" in plan.describe()

    def test_a_depth_the_printer_can_show_is_left_alone(self):
        plan = plan_detail(0.6, 50_000, 60.0)

        assert plan.amplitude_mm == pytest.approx(0.6)
        assert not plan.was_clamped

    def test_a_finer_layer_height_lets_shallower_detail_through(self):
        assert plan_detail(0.1, 50_000, 60.0, layer_height_mm=0.08).amplitude_mm == pytest.approx(
            0.1
        )

    def test_relief_that_would_swallow_the_model_is_refused_with_a_number(self):
        """Thirty millimetres on a forty millimetre model is a typo, not a request."""
        plan = plan_detail(30.0, 50_000, 40.0)

        assert not plan.is_worth_doing
        assert "10.0 mm" in plan.refused

    def test_there_is_nothing_to_do_without_a_model(self):
        assert not plan_detail(0.5, 0, 60.0).is_worth_doing
        assert not plan_detail(0.5, 50_000, 0.0).is_worth_doing


class TestHowMuchTheMeshNeedsToGrow:
    def test_a_coarse_mesh_is_subdivided_before_anything_is_baked_into_it(self):
        """A feature needs two vertices across it or it does not exist."""
        assert plan_detail(0.4, 1_000, 60.0).subdivisions > 0

    def test_a_mesh_already_fine_enough_is_left_alone(self):
        """Subdividing quadruples the geometry; doing it for nothing is not free."""
        assert plan_detail(0.4, 500_000, 60.0).subdivisions == 0

    def test_a_coarser_mesh_needs_more_passes_than_a_finer_one(self):
        coarse = plan_detail(0.4, 1_000, 60.0).subdivisions
        fine = plan_detail(0.4, 100_000, 60.0).subdivisions
        assert coarse > fine

    def test_a_bigger_model_at_the_same_triangle_count_needs_more_passes(self):
        """The same triangles spread over more surface hold coarser detail."""
        small = plan_detail(0.4, 50_000, 30.0).subdivisions
        large = plan_detail(0.4, 50_000, 200.0).subdivisions
        assert large >= small

    def test_it_never_subdivides_away_the_afternoon(self):
        assert plan_detail(0.4, 4, 250.0).subdivisions <= MAX_SUBDIVISIONS

    def test_it_never_grows_past_what_the_viewport_can_hold(self):
        plan = plan_detail(0.4, 1_000_000, 250.0)
        assert plan.subdivisions == 0, "quadrupling a million triangles helps nobody"

    def test_a_wider_nozzle_asks_for_less_of_the_mesh(self):
        """There is no point holding detail an 0.8 mm nozzle cannot lay down."""
        fine = plan_detail(0.4, 20_000, 60.0, Nozzle.FINE).subdivisions
        wide = plan_detail(0.4, 20_000, 60.0, Nozzle.EXTRA_WIDE).subdivisions
        assert fine >= wide


class TestWhatTheUserIsTold:
    def test_it_says_how_deep_and_how_much_growing_is_needed(self):
        told = plan_detail(0.6, 1_000, 60.0).describe()

        assert "0.60 mm deep" in told
        assert "subdividing" in told

    def test_one_pass_reads_as_singular(self):
        plan = plan_detail(0.6, 100_000, 60.0)
        if plan.subdivisions == 1:
            assert "1 time." in plan.describe()

    def test_a_mesh_that_needs_no_growing_says_nothing_about_it(self):
        assert "subdividing" not in plan_detail(0.4, 500_000, 60.0).describe()

    def test_a_refusal_is_the_whole_message(self):
        plan = plan_detail(30.0, 50_000, 40.0)
        assert plan.describe() == plan.refused
