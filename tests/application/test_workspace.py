"""The use-case layer, driven entirely through fake ports.

No files, no slicer, no display. If this ever needs a real dependency, the
layering has gone wrong.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from modelpop.application.ports import SliceJob, SliceReport
from modelpop.application.workspace import Workspace, WorkspaceState
from modelpop.domain import Length, Mesh, Unit
from modelpop.domain.printer import PrinterProfile, SupportType
from modelpop.domain.readiness import MeshFacts
from modelpop.domain.result import Result, failure, success


def box(size: float = 20.0) -> Mesh:
    half = size / 2
    vertices = np.array(
        [
            [-half, -half, 0],
            [half, -half, 0],
            [half, half, 0],
            [-half, half, 0],
            [-half, -half, size],
            [half, -half, size],
            [half, half, size],
            [-half, half, size],
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


# --------------------------------------------------------------------- fakes


@dataclass
class FakeIO:
    """Mesh IO that never touches a disk."""

    to_load: Mesh | None = field(default_factory=box)
    load_error: str = ""
    saved: list[tuple[Mesh, Path]] = field(default_factory=list)

    def load(self, path: Path) -> Result[Mesh]:
        if self.load_error:
            return failure(self.load_error)
        assert self.to_load is not None
        return success(self.to_load)

    def save(self, mesh: Mesh, path: Path) -> Result[Path]:
        self.saved.append((mesh, path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pretend this is a mesh")
        return success(path)

    def supported_suffixes(self) -> frozenset[str]:
        return frozenset({".stl"})


@dataclass
class FakeOps:
    """Geometry operations with controllable outcomes."""

    watertight: bool = True
    overhangs: float = 0.0
    repair_error: str = ""

    def inspect(self, mesh: Mesh, *, measure_walls: bool = True) -> MeshFacts:
        return MeshFacts(
            mesh=mesh,
            is_watertight=self.watertight,
            is_winding_consistent=True,
            overhang_area_fraction=self.overhangs,
        )

    def repair(self, mesh: Mesh) -> Result[Mesh]:
        if self.repair_error:
            return failure(self.repair_error)
        self.watertight = True
        return success(mesh)

    def normalise(self, mesh: Mesh) -> Mesh:
        return mesh

    def decimate(self, mesh: Mesh, target_triangles: int) -> Result[Mesh]:
        return success(mesh)

    def union(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        return success(left)

    def difference(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        return success(left)

    def intersection(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        return success(left)


@dataclass
class FakeSlicer:
    """A slicer that records what it was asked to do."""

    available: bool = True
    jobs: list[SliceJob] = field(default_factory=list)
    fail_with: str = ""
    fail_only_with_supports: bool = False

    def is_available(self) -> bool:
        return self.available

    def describe(self) -> str:
        return "fake slicer"

    def slice(self, job: SliceJob) -> Result[SliceReport]:
        self.jobs.append(job)
        if self.fail_only_with_supports and job.supports is not SupportType.NONE:
            return failure(
                "Slicing failed",
                "One of the plate is empty or has no object fully inside it.",
            )
        if self.fail_with:
            return failure("Slicing failed", self.fail_with)
        return success(
            SliceReport(
                succeeded=True,
                message="Success.",
                predicted_seconds=600.0,
                supports_generated=job.supports is not SupportType.NONE,
            )
        )


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(FakeIO(), FakeOps(), FakeSlicer(), PrinterProfile.p2s())


# --------------------------------------------------------------------- tests


class TestOpening:
    def test_a_model_is_assessed_as_soon_as_it_is_opened(self, workspace):
        """The user should learn a download is broken before trying to print it."""
        state = workspace.open(Path("model.stl")).unwrap()
        assert state.has_model
        assert state.readiness is not None

    def test_a_broken_model_is_reported_as_unprintable_on_open(self):
        ws = Workspace(FakeIO(), FakeOps(watertight=False), FakeSlicer())
        state = ws.open(Path("broken.stl")).unwrap()
        assert not state.readiness.is_printable

    def test_a_read_failure_is_passed_through(self):
        ws = Workspace(FakeIO(load_error="Could not read it"), FakeOps())
        result = ws.open(Path("nope.stl"))
        assert not result.ok
        assert "Could not read it" in result.error

    def test_a_generated_mesh_can_be_adopted_without_a_file(self, workspace):
        state = workspace.adopt(box())
        assert state.has_model
        assert state.source_path is None
        assert state.title == "Untitled"


class TestState:
    def test_an_empty_workspace_describes_itself_honestly(self):
        assert WorkspaceState().describe() == "No model loaded."
        assert WorkspaceState().title == "No model"

    def test_the_summary_names_the_size_the_user_cares_about(self, workspace):
        state = workspace.adopt(box(20))
        assert "12 triangles" in state.describe()
        assert "20.0 mm" in state.describe()

    def test_the_title_is_the_file_name(self, workspace):
        assert workspace.open(Path("/models/dragon.stl")).unwrap().title == "dragon.stl"


class TestTransforms:
    def test_repair_makes_the_model_printable_and_re_assesses(self):
        ws = Workspace(FakeIO(), FakeOps(watertight=False), FakeSlicer())
        broken = ws.open(Path("broken.stl")).unwrap()
        assert not broken.readiness.is_printable

        repaired = ws.repair(broken).unwrap()
        assert repaired.readiness.is_printable

    def test_a_repair_failure_is_reported_not_raised(self):
        ws = Workspace(FakeIO(), FakeOps(watertight=False, repair_error="beyond saving"))
        result = ws.repair(ws.open(Path("x.stl")).unwrap())
        assert not result.ok
        assert "beyond saving" in result.error

    def test_scaling_to_six_inches_does_what_the_brief_asks(self, workspace):
        state = workspace.adopt(box(20))
        scaled = workspace.scale_to_fit(state, Length.inches(6)).unwrap()
        assert scaled.mesh.bounds.largest_dimension.millimetres == pytest.approx(152.4)

    def test_a_nonsense_target_size_is_refused(self, workspace):
        state = workspace.adopt(box())
        assert not workspace.scale_to_fit(state, Length.mm(0)).ok
        assert not workspace.scale_to_fit(state, Length.mm(-5)).ok

    def test_preparing_for_the_bed_seats_the_model_on_z_zero(self, workspace):
        state = workspace.adopt(box(20).translated(0, 0, 50))
        prepared = workspace.prepare_for_bed(state).unwrap()
        assert prepared.mesh.bounds.min_z == pytest.approx(0.0)

    def test_every_transform_refuses_politely_with_no_model(self, workspace):
        empty = WorkspaceState()
        for attempt in (
            lambda: workspace.repair(empty),
            lambda: workspace.prepare_for_bed(empty),
            lambda: workspace.decimate(empty),
            lambda: workspace.scale_to_fit(empty, Length.mm(10)),
            lambda: workspace.save(empty, Path("x.stl")),
        ):
            result = attempt()
            assert not result.ok
            assert "no model is open" in result.error

    def test_a_transform_invalidates_a_stale_slice(self, workspace, tmp_path):
        state = workspace.adopt(box())
        sliced = workspace.slice(state, tmp_path).unwrap()
        assert sliced.last_slice is not None

        rescaled = workspace.scale_to_fit(sliced, Length.mm(50)).unwrap()
        assert rescaled.last_slice is None, "the old G-code no longer matches the model"


class TestSlicing:
    def test_a_model_is_moved_into_printer_coordinates_before_slicing(self, tmp_path):
        """Our origin is the bed centre; Bambu's is a corner."""
        io = FakeIO()
        ws = Workspace(io, FakeOps(), FakeSlicer(), PrinterProfile.p2s())
        state = ws.adopt(box(20))
        ws.slice(state, tmp_path)

        written, _ = io.saved[-1]
        centre_x, centre_y = PrinterProfile.p2s().bed_centre
        assert written.bounds.centre[0] == pytest.approx(centre_x)
        assert written.bounds.centre[1] == pytest.approx(centre_y)
        assert written.bounds.min_z == pytest.approx(0.0)

    def test_supports_are_skipped_when_nothing_overhangs(self, tmp_path):
        slicer = FakeSlicer()
        ws = Workspace(FakeIO(), FakeOps(overhangs=0.0), slicer)
        ws.slice(ws.adopt(box()), tmp_path)
        assert slicer.jobs[-1].supports is SupportType.NONE

    def test_supports_are_requested_when_the_model_overhangs(self, tmp_path):
        slicer = FakeSlicer()
        ws = Workspace(FakeIO(), FakeOps(overhangs=0.25), slicer)
        ws.slice(ws.adopt(box()), tmp_path)
        assert slicer.jobs[-1].supports is SupportType.TREE_AUTO

    def test_an_explicit_choice_overrides_the_automatic_one(self, tmp_path):
        slicer = FakeSlicer()
        ws = Workspace(FakeIO(), FakeOps(overhangs=0.0), slicer)
        ws.slice(ws.adopt(box()), tmp_path, supports=SupportType.TREE_AUTO)
        assert slicer.jobs[-1].supports is SupportType.TREE_AUTO

    def test_supports_are_dropped_rather_than_failing_when_they_will_not_fit(self, tmp_path):
        """Supports enlarge the footprint, and a big model can stop fitting."""
        slicer = FakeSlicer(fail_only_with_supports=True)
        ws = Workspace(FakeIO(), FakeOps(overhangs=0.3), slicer)

        result = ws.slice(ws.adopt(box()), tmp_path)

        assert result.ok, "it should retry without supports rather than give up"
        assert len(slicer.jobs) == 2
        assert slicer.jobs[1].supports is SupportType.NONE
        assert any("Supports were turned off" in w for w in result.unwrap().last_slice.warnings)

    def test_an_unprintable_model_is_never_sent_to_the_slicer(self, tmp_path):
        """Sending known-broken geometry teaches the user to ignore warnings."""
        slicer = FakeSlicer()
        ws = Workspace(FakeIO(), FakeOps(watertight=False), slicer)
        result = ws.slice(ws.open(Path("broken.stl")).unwrap(), tmp_path)

        assert not result.ok
        assert "not ready to print" in result.error
        assert slicer.jobs == [], "the slicer should not have been called at all"

    def test_a_missing_slicer_is_reported_actionably(self, tmp_path):
        ws = Workspace(FakeIO(), FakeOps(), FakeSlicer(available=False))
        result = ws.slice(ws.adopt(box()), tmp_path)
        assert not result.ok
        assert "Install it" in result.error

    def test_no_slicer_configured_at_all_is_not_a_crash(self, tmp_path):
        ws = Workspace(FakeIO(), FakeOps(), slicer=None)
        result = ws.slice(ws.adopt(box()), tmp_path)
        assert not result.ok
        assert "No slicer available" in result.error

    def test_a_genuine_slicer_failure_is_passed_through(self, tmp_path):
        ws = Workspace(FakeIO(), FakeOps(), FakeSlicer(fail_with="the printer exploded"))
        result = ws.slice(ws.adopt(box()), tmp_path)
        assert not result.ok
        assert "exploded" in result.error


class TestUnits:
    def test_a_model_in_inches_is_assessed_at_its_real_size(self, workspace):
        """An 11-inch model is 279 mm and does not fit, however small the number looks."""
        in_inches = box(11).with_unit(Unit.INCH)
        state = workspace.adopt(in_inches)
        assert not state.readiness.is_printable
        assert any(f.rule == "fits-build-volume" for f in state.readiness.findings)
