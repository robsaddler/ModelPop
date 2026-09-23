import numpy as np
import pytest

from modelpop.domain import Length, Mesh, Unit
from modelpop.domain.printer import Nozzle, PrinterProfile
from modelpop.domain.readiness import (
    FitsBuildVolume,
    MeshFacts,
    Severity,
    TipOverRisk,
    WallThickness,
    Watertight,
    assess,
    standard_rules,
)


def box_mesh(size: float = 20.0, height: float | None = None) -> Mesh:
    h = size if height is None else height
    vertices = np.array(
        [
            [0, 0, 0],
            [size, 0, 0],
            [size, size, 0],
            [0, size, 0],
            [0, 0, h],
            [size, 0, h],
            [size, size, h],
            [0, size, h],
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
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, 6],
            [3, 0, 4],
            [3, 4, 7],
        ],
        dtype=np.int32,
    )
    return Mesh(vertices, faces)


def healthy(mesh: Mesh | None = None, **overrides) -> MeshFacts:
    """A sound mesh, with any single fact overridden to make it unsound."""
    defaults = {
        "mesh": mesh if mesh is not None else box_mesh(),
        "is_watertight": True,
        "is_winding_consistent": True,
    }
    return MeshFacts(**{**defaults, **overrides})


P2S = PrinterProfile.p2s()


class TestVerdict:
    def test_a_healthy_model_is_ready(self):
        report = assess(healthy(), P2S)
        assert report.verdict is Severity.INFO
        assert report.is_printable
        assert report.summary() == "Ready to print."

    def test_a_blocker_stops_the_print(self):
        report = assess(healthy(is_watertight=False), P2S)
        assert report.verdict is Severity.BLOCKER
        assert not report.is_printable
        assert "Cannot print" in report.summary()

    def test_warnings_do_not_stop_the_print(self):
        report = assess(healthy(shell_count=3), P2S)
        assert report.verdict is Severity.WARNING
        assert report.is_printable
        assert "Printable, with" in report.summary()

    def test_the_worst_finding_wins(self):
        report = assess(healthy(is_watertight=False, shell_count=4), P2S)
        assert report.verdict is Severity.BLOCKER

    def test_findings_are_ordered_worst_first(self):
        report = assess(healthy(is_watertight=False, shell_count=3, degenerate_face_count=5), P2S)
        severities = [f.severity for f in report.findings]
        assert severities == sorted(severities, reverse=True)

    def test_an_empty_model_is_blocked(self):
        report = assess(
            MeshFacts(mesh=Mesh.empty(), is_watertight=False, is_winding_consistent=True), P2S
        )
        assert not report.is_printable
        assert any("no geometry" in f.message for f in report.findings)


class TestEveryFindingIsActionable:
    """A warning the user cannot act on is noise."""

    @pytest.mark.parametrize(
        "facts",
        [
            healthy(is_watertight=False),
            healthy(is_winding_consistent=False),
            healthy(box_mesh(400)),
            healthy(shell_count=3),
            healthy(self_intersection_count=7),
            healthy(thinnest_wall=Length.mm(0.2)),
            healthy(box_mesh(5, height=200)),
            healthy(degenerate_face_count=3),
        ],
    )
    def test_every_finding_names_a_remedy_and_a_stage(self, facts):
        findings = assess(facts, P2S).findings
        assert findings, "expected this to produce a finding"
        for finding in findings:
            assert finding.remedy, f"{finding.rule} gives the user nothing to do"
            assert finding.fix_stage or finding.severity is Severity.BLOCKER

    def test_messages_avoid_jargon_the_user_cannot_act_on(self):
        finding = Watertight().check(healthy(is_watertight=False, hole_count=12), P2S)
        assert finding is not None
        assert "12 hole" in finding.message
        assert "repair" in finding.remedy.lower()


class TestBuildVolume:
    def test_a_model_that_fits_passes(self):
        assert FitsBuildVolume().check(healthy(box_mesh(200)), P2S) is None

    def test_a_model_that_does_not_fit_is_blocked(self):
        finding = FitsBuildVolume().check(healthy(box_mesh(300)), P2S)
        assert finding is not None
        assert finding.severity is Severity.BLOCKER
        assert "256" in finding.message

    def test_a_model_in_inches_is_judged_by_its_real_size(self):
        # 11 inches is 279 mm: too big, even though the number looks small
        in_inches = box_mesh(11).with_unit(Unit.INCH)
        finding = FitsBuildVolume().check(healthy(in_inches), P2S)
        assert finding is not None and finding.severity is Severity.BLOCKER

    def test_the_six_inch_dragon_from_the_brief_fits(self):
        dragon = box_mesh(1).scaled_to_fit(Length.inches(6))
        assert FitsBuildVolume().check(healthy(dragon), P2S) is None


class TestWallThicknessRule:
    def test_detail_below_two_line_widths_is_flagged(self):
        finding = WallThickness().check(healthy(thinnest_wall=Length.mm(0.3)), P2S)
        assert finding is not None
        assert finding.severity is Severity.WARNING
        assert "0.30 mm" in finding.message

    def test_a_thick_enough_wall_passes(self):
        assert WallThickness().check(healthy(thinnest_wall=Length.mm(2.0)), P2S) is None

    def test_a_finer_nozzle_accepts_finer_detail(self):
        fine = PrinterProfile.p2s(nozzle=Nozzle.FINE)
        thin = healthy(thinnest_wall=Length.mm(0.5))
        assert WallThickness().check(thin, P2S) is not None
        assert WallThickness().check(thin, fine) is None

    def test_unmeasured_walls_produce_no_finding(self):
        assert WallThickness().check(healthy(thinnest_wall=None), P2S) is None


class TestTipOver:
    def test_a_tall_thin_model_is_flagged(self):
        finding = TipOverRisk().check(healthy(box_mesh(5, height=200)), P2S)
        assert finding is not None
        assert "taller than" in finding.message

    def test_a_squat_model_passes(self):
        assert TipOverRisk().check(healthy(box_mesh(50, height=40)), P2S) is None


class TestRuleSet:
    def test_every_standard_rule_has_a_name(self):
        assert all(rule.name for rule in standard_rules())

    def test_rule_names_are_unique(self):
        names = [rule.name for rule in standard_rules()]
        assert len(names) == len(set(names))

    def test_rules_never_raise_on_an_empty_mesh(self):
        facts = MeshFacts(mesh=Mesh.empty(), is_watertight=False, is_winding_consistent=True)
        for rule in standard_rules():
            rule.check(facts, P2S)  # must not raise

    def test_a_custom_rule_set_is_honoured(self):
        report = assess(healthy(is_watertight=False), P2S, rules=(TipOverRisk(),))
        assert report.findings == ()
