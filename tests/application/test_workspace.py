"""The use-case layer, driven entirely through fake ports.

No files, no slicer, no display. If this ever needs a real dependency, the
layering has gone wrong.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pytest

from modelpop.application.ports import SliceJob, SliceReport
from modelpop.application.workspace import Workspace, WorkspaceState
from modelpop.domain import Length, Mesh, Unit
from modelpop.domain.printer import PrinterProfile, SupportType
from modelpop.domain.readiness import Finding, MeshFacts, Severity
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
    emits_gcode: bool = False

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
                gcode_path=job.output_dir / "plate_1.gcode" if self.emits_gcode else None,
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


@dataclass
class FakeVerifier:
    """A toolpath verifier that reports whatever the test asks for."""

    findings: tuple[Finding, ...] = ()
    calls: list[Path] = field(default_factory=list)

    def verify(self, gcode: Path, printer: PrinterProfile) -> tuple[Finding, ...]:
        self.calls.append(gcode)
        return self.findings


ISLAND = Finding(
    "unsupported-island",
    Severity.WARNING,
    "Layer 40 at 8.00 mm starts about 300 mm2 of material in mid-air.",
    "Turn supports on.",
)


class TestToolpathVerification:
    """Slicing is the only moment the toolpath exists, so it is checked there."""

    def workspace(self, verifier=None, slicer=None) -> Workspace:
        return Workspace(
            FakeIO(),
            FakeOps(),
            slicer or FakeSlicer(emits_gcode=True),
            PrinterProfile.p2s(),
            gcode_verifier=verifier,
        )

    def opened(self, ws: Workspace) -> WorkspaceState:
        return ws.open(Path("part.stl")).unwrap()

    def test_the_toolpath_is_read_back_after_a_slice(self, tmp_path):
        verifier = FakeVerifier()
        ws = self.workspace(verifier)
        ws.slice(self.opened(ws), tmp_path)
        assert verifier.calls, "the toolpath was never checked"

    def test_a_toolpath_finding_reaches_the_readiness_report(self, tmp_path):
        """Otherwise the check runs and the user never hears about it."""
        ws = self.workspace(FakeVerifier(findings=(ISLAND,)))
        state = ws.slice(self.opened(ws), tmp_path).unwrap()

        assert state.readiness is not None
        assert any(f.rule == "unsupported-island" for f in state.readiness.findings)

    def test_a_clean_toolpath_leaves_the_report_alone(self, tmp_path):
        ws = self.workspace(FakeVerifier())
        before = self.opened(ws)
        after = ws.slice(before, tmp_path).unwrap()
        assert after.readiness == before.readiness

    def test_slicing_twice_does_not_stack_the_same_complaint(self, tmp_path):
        """A re-slice replaces the previous verdict; it does not append to it."""
        ws = self.workspace(FakeVerifier(findings=(ISLAND,)))
        once = ws.slice(self.opened(ws), tmp_path).unwrap()
        twice = ws.slice(once, tmp_path).unwrap()

        assert twice.readiness is not None
        islands = [f for f in twice.readiness.findings if f.rule == "unsupported-island"]
        assert len(islands) == 1

    def test_mesh_findings_survive_a_slice(self, tmp_path):
        """Verification adds to what is known; it must not erase it."""
        ws = Workspace(
            FakeIO(),
            FakeOps(overhangs=0.4),  # enough to raise an overhang finding
            FakeSlicer(emits_gcode=True),
            PrinterProfile.p2s(),
            gcode_verifier=FakeVerifier(findings=(ISLAND,)),
        )
        opened = self.opened(ws)
        assert opened.readiness is not None and opened.readiness.findings
        state = ws.slice(opened, tmp_path).unwrap()

        assert state.readiness is not None
        rules = {f.rule for f in state.readiness.findings}
        assert "unsupported-island" in rules
        assert len(rules) > 1, "the mesh findings were thrown away"

    def test_a_slicer_that_produced_no_gcode_is_not_verified(self, tmp_path):
        verifier = FakeVerifier()
        ws = self.workspace(verifier, FakeSlicer(emits_gcode=False))
        assert ws.slice(self.opened(ws), tmp_path).ok
        assert verifier.calls == []

    def test_without_a_verifier_slicing_still_works(self, tmp_path):
        """An extra check that is absent must not turn a good slice into a failure."""
        ws = self.workspace(verifier=None)
        assert ws.slice(self.opened(ws), tmp_path).ok


class TestRescuingTheDetail:
    """Baking a model's colour into its surface, at the use-case level.

    The mechanics are tested against real textures in tests/geometry. What
    matters here is *when the button appears*: a model that never had a texture
    cannot have one baked, and offering it anyway puts an operation in front of
    the user that can only fail.
    """

    class FakeBake:
        def __init__(self, mesh=None, error=""):
            self.asked: list[tuple] = []
            self._mesh = mesh
            self._error = error

        def is_available(self) -> bool:
            return True

        def describe(self) -> str:
            return "a fake baker"

        def rescue(self, source, depth_mm=0.4, nozzle=None, layer_height_mm=0.2):
            from modelpop.domain.result import failure as fail
            from modelpop.domain.result import success as ok

            self.asked.append((source, depth_mm))
            if self._error:
                return fail("no", self._error)
            return ok(self._mesh if self._mesh is not None else box(20))

    def workspace(self, baker=None) -> Workspace:
        return Workspace(mesh_io=FakeIO(), mesh_ops=FakeOps(), detail=baker)

    def textured(self, tmp_path) -> WorkspaceState:
        source = tmp_path / "generated.glb"
        source.write_bytes(b"pretend this is a glb")
        return WorkspaceState(mesh=box(20), source_path=source, textured_path=source)

    def test_without_a_baker_it_says_so_rather_than_failing_obscurely(self, tmp_path):
        outcome = self.workspace().rescue_detail(self.textured(tmp_path))
        assert not outcome.ok
        assert "not available" in outcome.error

    def test_a_model_with_no_texture_is_refused_before_anything_is_read(self, tmp_path):
        baker = self.FakeBake()
        outcome = self.workspace(baker).rescue_detail(WorkspaceState(mesh=box(20)))

        assert not outcome.ok
        assert "no texture to bake" in outcome.error
        assert baker.asked == [], "it reached the baker anyway"

    def test_a_texture_whose_file_has_gone_is_refused(self, tmp_path):
        state = WorkspaceState(mesh=box(20), textured_path=tmp_path / "gone.glb")
        assert not self.workspace(self.FakeBake()).rescue_detail(state).ok

    def test_it_reads_the_textured_file_rather_than_the_open_mesh(self, tmp_path):
        """The open mesh may already have been repaired, scaled or simplified,
        and the texture's coordinates would not fit it any more."""
        baker = self.FakeBake()
        state = self.textured(tmp_path)
        self.workspace(baker).rescue_detail(state, 0.6)

        assert baker.asked == [(state.textured_path, 0.6)]

    def test_the_baked_model_replaces_the_open_one_and_is_re_assessed(self, tmp_path):
        baked = box(40)
        outcome = self.workspace(self.FakeBake(baked)).rescue_detail(self.textured(tmp_path))

        assert outcome.ok
        after = outcome.unwrap()
        assert after.mesh is baked
        assert after.readiness is not None, "the bake changed the geometry and nothing re-checked"

    def test_a_stale_slice_is_dropped_because_the_geometry_changed(self, tmp_path):
        from modelpop.application.ports import SliceReport

        state = replace(self.textured(tmp_path), last_slice=SliceReport(True, "ok"))
        after = self.workspace(self.FakeBake()).rescue_detail(state).unwrap()

        assert after.last_slice is None

    def test_the_bake_is_recorded_beside_where_the_shape_came_from(self, tmp_path):
        """Both facts are true and both are worth having six months later."""
        state = replace(self.textured(tmp_path), generation_note="Generated from a photo")
        after = self.workspace(self.FakeBake()).rescue_detail(state, 0.5).unwrap()

        assert "Generated from a photo" in after.generation_note
        assert "0.50 mm deep" in after.generation_note

    def test_a_refusal_from_the_baker_reaches_the_user_intact(self, tmp_path):
        baker = self.FakeBake(error="it was all one colour")
        outcome = self.workspace(baker).rescue_detail(self.textured(tmp_path))

        assert not outcome.ok
        assert "one colour" in outcome.error

    def test_opening_a_format_that_can_carry_a_texture_offers_the_bake(self, tmp_path):
        source = tmp_path / "model.glb"
        source.write_bytes(b"x")
        opened = self.workspace(self.FakeBake()).open(source)

        assert opened.unwrap().textured_path == source

    def test_opening_an_stl_offers_nothing_because_it_can_carry_no_texture(self, tmp_path):
        """A button that can only ever fail is worse than no button."""
        source = tmp_path / "model.stl"
        source.write_bytes(b"x")
        opened = self.workspace(self.FakeBake()).open(source)

        assert opened.unwrap().textured_path is None


class TestMeasuringFromPhotographs:
    """Reconstruction at the use-case level, with a fake in place of two CLIs.

    The distinction this layer has to keep straight is between *measuring* a
    shape from several photographs and asking a model to *invent* one from a
    single picture. They arrive at the same place and mean different things,
    and the provenance is where that difference survives.
    """

    class FakeReconstructor:
        def __init__(self, mesh=None, error=""):
            self.asked: list[tuple] = []
            self._mesh = mesh
            self._error = error

        def is_available(self) -> bool:
            return True

        def describe(self) -> str:
            return "a fake reconstructor"

        def reconstruct(self, photos, options=None, on_progress=None):
            from modelpop.application.reconstruction_ports import Reconstruction
            from modelpop.domain.result import failure as fail
            from modelpop.domain.result import success as ok

            self.asked.append((photos, options))
            if self._error:
                return fail("no", self._error)
            if on_progress is not None:
                on_progress(0.5, "halfway")
            return ok(
                Reconstruction(
                    mesh=self._mesh if self._mesh is not None else box(20),
                    photos_given=len(photos),
                    photos_used=len(photos),
                    seconds=90.0,
                )
            )

    def workspace(self, reconstructor=None) -> Workspace:
        return Workspace(mesh_io=FakeIO(), mesh_ops=FakeOps(), reconstructor=reconstructor)

    def photos(self, tmp_path, count: int = 24):
        from modelpop.domain.photo_set import PhotoSet

        for index in range(count):
            (tmp_path / f"p{index:03d}.jpg").write_bytes(b"x")
        return PhotoSet.of(tmp_path.glob("*.jpg"))

    def test_without_the_tools_it_says_so_rather_than_failing_obscurely(self, tmp_path):
        outcome = self.workspace().reconstruct_from_photos(self.photos(tmp_path))

        assert not outcome.ok
        assert "unavailable" in outcome.error

    def test_the_measured_model_lands_in_the_workspace(self, tmp_path):
        outcome = self.workspace(self.FakeReconstructor()).reconstruct_from_photos(
            self.photos(tmp_path)
        )

        assert outcome.ok
        assert outcome.unwrap().has_model

    def test_it_is_assessed_on_arrival_like_anything_else(self, tmp_path):
        """Everything downstream works on it without knowing a camera was involved."""
        after = (
            self.workspace(self.FakeReconstructor())
            .reconstruct_from_photos(self.photos(tmp_path))
            .unwrap()
        )
        assert after.readiness is not None

    def test_the_provenance_says_it_was_measured_not_invented(self, tmp_path):
        """The whole question six months later."""
        after = (
            self.workspace(self.FakeReconstructor())
            .reconstruct_from_photos(self.photos(tmp_path))
            .unwrap()
        )
        assert "Reconstructed from 24 of 24 photographs" in after.generation_note

    def test_a_reconstruction_has_no_texture_to_bake(self, tmp_path):
        """The mesher writes bare geometry, so detail rescue is not offered."""
        after = (
            self.workspace(self.FakeReconstructor())
            .reconstruct_from_photos(self.photos(tmp_path))
            .unwrap()
        )
        assert after.textured_path is None

    def test_the_options_reach_the_reconstructor_untouched(self, tmp_path):
        from modelpop.application.reconstruction_ports import Quality, ReconstructionOptions

        fake = self.FakeReconstructor()
        wanted = ReconstructionOptions(quality=Quality.FINE)
        self.workspace(fake).reconstruct_from_photos(self.photos(tmp_path), wanted)

        assert fake.asked[0][1] is wanted

    def test_progress_is_passed_through(self, tmp_path):
        seen: list[tuple] = []
        self.workspace(self.FakeReconstructor()).reconstruct_from_photos(
            self.photos(tmp_path), None, lambda f, w: seen.append((f, w))
        )
        assert seen == [(0.5, "halfway")]

    def test_a_refusal_reaches_the_user_intact(self, tmp_path):
        fake = self.FakeReconstructor(error="the photographs did not overlap")
        outcome = self.workspace(fake).reconstruct_from_photos(self.photos(tmp_path))

        assert not outcome.ok
        assert "overlap" in outcome.error
