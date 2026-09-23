"""The gates a generated part must clear.

Driven with synthetic results, so the whole suite runs in milliseconds with no
CAD kernel. The kernel itself is covered separately.
"""

import numpy as np

from modelpop.application.cad_ports import (
    Dimension,
    DimensionTable,
    ScriptResult,
    SolidMeasurements,
)
from modelpop.domain import Length, Mesh
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.readiness import MeshFacts
from modelpop.generation import Gate, evaluate


def mesh() -> Mesh:
    return Mesh(
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64),
        np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int32),
    )


def result(
    *,
    volume: float = 18889.0,
    width: float = 60.0,
    depth: float = 40.0,
    height: float = 8.0,
    solids: int = 1,
) -> ScriptResult:
    return ScriptResult(
        mesh=mesh(),
        measurements=SolidMeasurements(
            volume_mm3=volume,
            width=Length.mm(width),
            depth=Length.mm(depth),
            height=Length.mm(height),
            solid_count=solids,
            face_count=12,
            edge_count=30,
            vertex_count=20,
        ),
    )


def facts(*, watertight: bool = True) -> MeshFacts:
    return MeshFacts(mesh=mesh(), is_watertight=watertight, is_winding_consistent=True)


BRACKET_SPEC = DimensionTable(
    (
        Dimension("width", Length.mm(60)),
        Dimension("depth", Length.mm(40)),
        Dimension("height", Length.mm(8)),
    )
)


class TestPassing:
    def test_a_correct_part_clears_every_gate(self):
        report = evaluate(result(), BRACKET_SPEC, facts=facts())
        assert report.passed
        assert report.score == 1.0
        assert report.feedback == ""

    def test_the_report_reads_as_a_checklist(self):
        text = str(evaluate(result(), BRACKET_SPEC, facts=facts()))
        assert "the script runs" in text
        assert "the part measures what was asked for" in text


class TestFailing:
    def test_no_volume_is_caught_first(self):
        report = evaluate(result(volume=0.0), BRACKET_SPEC, facts=facts())
        assert not report.passed
        assert report.first_failure.gate is Gate.HAS_VOLUME
        assert "cancelled each other out" in report.feedback

    def test_a_part_in_pieces_is_rejected(self):
        report = evaluate(result(solids=3), BRACKET_SPEC, facts=facts())
        assert report.first_failure.gate is Gate.SINGLE_SOLID
        assert "3 separate solids" in report.feedback

    def test_several_solids_are_allowed_when_an_assembly_was_asked_for(self):
        report = evaluate(result(solids=3), facts=facts(), expect_single_solid=False)
        assert report.passed

    def test_a_part_that_is_not_watertight_is_rejected(self):
        report = evaluate(result(), BRACKET_SPEC, facts=facts(watertight=False))
        assert report.first_failure.gate is Gate.WATERTIGHT
        assert "cannot be printed" in report.feedback

    def test_a_part_larger_than_the_printer_is_rejected(self):
        report = evaluate(result(width=400, depth=40, height=8), facts=facts())
        assert report.first_failure.gate is Gate.FITS_PRINTER
        assert "256 mm build volume" in report.feedback


class TestDimensionFeedback:
    """The point of the whole pipeline: numbers a model can act on."""

    def test_a_wrong_dimension_is_reported_with_the_measurement_and_the_target(self):
        report = evaluate(result(width=55.0), BRACKET_SPEC, facts=facts())
        assert report.first_failure.gate is Gate.DIMENSIONS
        assert "55.00 mm" in report.feedback
        assert "60.00 mm" in report.feedback
        assert "5.00 mm too small" in report.feedback

    def test_being_over_size_is_described_as_such(self):
        report = evaluate(result(width=66.0), BRACKET_SPEC, facts=facts())
        assert "6.00 mm too large" in report.feedback

    def test_a_dimension_inside_tolerance_is_accepted(self):
        report = evaluate(result(width=60.1), BRACKET_SPEC, facts=facts())
        assert report.passed

    def test_a_dimension_just_outside_tolerance_is_rejected(self):
        report = evaluate(result(width=60.3), BRACKET_SPEC, facts=facts())
        assert not report.passed

    def test_a_tighter_tolerance_is_honoured(self):
        precise = DimensionTable((Dimension("width", Length.mm(60), Length.mm(0.01)),))
        assert not evaluate(result(width=60.1), precise, facts=facts()).passed

    def test_several_wrong_dimensions_are_reported_together(self):
        report = evaluate(result(width=55, depth=35), BRACKET_SPEC, facts=facts())
        assert "width" in report.feedback
        assert "depth" in report.feedback

    def test_a_dimension_we_cannot_measure_is_skipped_not_guessed(self):
        """Feature-level dimensions need a measurement the worker does not take.

        Silently passing is right; inventing a number would be worse than useless.
        """
        spec = DimensionTable((Dimension("hole_spacing", Length.mm(40)),))
        assert evaluate(result(), spec, facts=facts()).passed


class TestFeedbackDiscipline:
    def test_only_the_first_failure_is_reported(self):
        """A model given six complaints at once produces a worse next attempt."""
        report = evaluate(result(volume=0.0, width=400, solids=5), BRACKET_SPEC, facts=facts())
        assert report.feedback.count(";") == 0
        assert len([r for r in report.results if not r.passed]) == 1

    def test_gates_after_a_failure_are_not_run(self):
        report = evaluate(result(volume=0.0), BRACKET_SPEC, facts=facts())
        gates_run = {r.gate for r in report.results}
        assert Gate.DIMENSIONS not in gates_run
        assert Gate.FITS_PRINTER not in gates_run

    def test_every_failure_says_something_a_model_could_act_on(self):
        for broken in (
            result(volume=0.0),
            result(solids=4),
            result(width=999),
            result(width=55),
        ):
            report = evaluate(broken, BRACKET_SPEC, facts=facts())
            if not report.passed:
                assert len(report.feedback) > 30, "feedback must be specific enough to act on"


class TestScoring:
    def test_score_orders_attempts_for_best_of_n(self):
        good = evaluate(result(), BRACKET_SPEC, facts=facts())
        near = evaluate(result(width=55), BRACKET_SPEC, facts=facts())
        bad = evaluate(result(volume=0.0), BRACKET_SPEC, facts=facts())
        assert good.score > near.score > bad.score

    def test_a_perfect_run_scores_one(self):
        assert evaluate(result(), BRACKET_SPEC, facts=facts()).score == 1.0


class TestOptionalInputs:
    def test_watertightness_is_skipped_rather_than_guessed_when_unmeasured(self):
        report = evaluate(result(), BRACKET_SPEC)
        assert report.passed
        assert Gate.WATERTIGHT not in {r.gate for r in report.results}

    def test_no_dimension_table_means_no_dimension_gate(self):
        report = evaluate(result(width=12.3), facts=facts())
        assert report.passed

    def test_a_different_printer_changes_what_fits(self):
        small = PrinterProfile(
            build_width=Length.mm(100),
            build_depth=Length.mm(100),
            build_height=Length.mm(100),
        )
        # 150 mm fits a P2S comfortably and not a 100 mm machine at all
        long_part = result(width=150.0)
        assert evaluate(long_part, facts=facts()).passed
        assert not evaluate(long_part, printer=small, facts=facts()).passed
