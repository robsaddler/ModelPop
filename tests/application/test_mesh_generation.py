"""The lease on the graphics card, and the picture-to-mesh adapter.

The heavy part - a native binary and ten gigabytes of weights - is deliberately
absent here, and that is the point: the interesting cases are all about what
happens when it is *not* installed, which is the state of every fresh machine,
and about how specifically the app says which piece is missing.
"""

import json
import os
import time
from pathlib import Path

import pytest

from modelpop.application.mesh_generation_ports import (
    Detail,
    GeneratedMesh,
    GenerationOptions,
    MeshGenerator,
)
from modelpop.domain.mesh import Mesh
from modelpop.generation.gpu_lease import STALE_AFTER_SECONDS, GpuBusyError, GpuLease
from modelpop.generation.trellis_cli import (
    _WEIGHT_FILES,
    MODEL_NAME,
    TrellisCliGenerator,
    _report,
    find_trellis_cli,
    find_weights,
)
from modelpop.mesh import TrimeshIO


class TestTheLease:
    """One large model on the card at a time. Two is an out-of-memory error
    partway through, after the user has already waited."""

    def test_a_fresh_lease_is_free(self, tmp_path):
        assert GpuLease.beside(tmp_path).is_free

    def test_holding_it_marks_it_taken(self, tmp_path):
        lease = GpuLease.beside(tmp_path)
        with lease.held():
            assert not lease.is_free
            assert lease.holder == os.getpid()

    def test_it_is_released_afterwards(self, tmp_path):
        lease = GpuLease.beside(tmp_path)
        with lease.held():
            pass
        assert lease.is_free

    def test_it_is_released_even_when_the_work_raises(self, tmp_path):
        """Otherwise one failure locks the feature until the app restarts."""
        lease = GpuLease.beside(tmp_path)
        with pytest.raises(ValueError, match="boom"), lease.held():
            raise ValueError("boom")
        assert lease.is_free

    def test_taking_it_twice_is_refused(self, tmp_path):
        """A user can click twice, or a prompt and an image can be queued."""
        lease = GpuLease.beside(tmp_path)
        with lease.held(), pytest.raises(GpuBusyError), lease.held():
            pass

    def test_another_live_process_blocks_it(self, tmp_path):
        """A second copy of ModelPop, or a training script in another window."""
        lease = GpuLease.beside(tmp_path)
        lease.path.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}), encoding="utf-8")

        # a different pid that is alive: our own parent will do on any platform
        assert not lease.is_free

    def test_a_dead_process_does_not_block_it_forever(self, tmp_path):
        """The failure mode of every naive lock file."""
        lease = GpuLease.beside(tmp_path)
        lease.path.write_text(json.dumps({"pid": 999_999_999, "at": time.time()}), encoding="utf-8")
        assert lease.is_free

    def test_a_stale_lease_expires(self, tmp_path):
        lease = GpuLease.beside(tmp_path)
        lease.path.write_text(
            json.dumps({"pid": os.getpid(), "at": time.time() - STALE_AFTER_SECONDS - 10}),
            encoding="utf-8",
        )
        assert lease.is_free

    def test_a_corrupt_lock_file_reads_as_free(self, tmp_path):
        lease = GpuLease.beside(tmp_path)
        lease.path.write_text("not json", encoding="utf-8")
        assert lease.is_free

    def test_a_lock_file_that_cannot_be_written_does_not_stop_the_work(self, tmp_path):
        """The in-process lock still holds, which covers the common case."""
        blocked = tmp_path / "wall"
        blocked.write_text("not a directory", encoding="utf-8")

        lease = GpuLease.beside(blocked)
        ran = False
        with lease.held():
            ran = True
        assert ran

    def test_it_says_why_it_is_busy(self, tmp_path):
        lease = GpuLease.beside(tmp_path)
        with lease.held():
            assert "already running" in lease.describe()
        assert "free" in lease.describe()


class TestWhatAGeneratedMeshRecords:
    def test_it_says_where_the_shape_came_from(self):
        """Six months later, "did I make this or did a model?" has no other
        answer."""
        generated = GeneratedMesh(
            mesh=Mesh.empty(), model="trellis", seed=7, source_image=Path("dragon.png")
        )
        assert "dragon.png" in generated.provenance
        assert "trellis" in generated.provenance
        assert "seed 7" in generated.provenance

    def test_the_detail_settings_explain_the_trade(self):
        for detail in Detail:
            assert detail.describe

    def test_a_triangle_ceiling_is_set_by_default(self):
        """These models produce millions and a printer cannot use them."""
        assert 0 < GenerationOptions().target_triangles <= 1_000_000


class TestThroughTheWorkspace:
    """The seam between a generated shape and everything downstream."""

    def workspace(self, generator=None):
        from modelpop.application.workspace import Workspace

        from .test_workspace import FakeIO, FakeOps, FakeSlicer

        return Workspace(FakeIO(), FakeOps(), FakeSlicer(), mesh_generator=generator)

    def fake_generator(self, result):
        class Fake:
            def is_available(self) -> bool:
                return True

            def describe(self) -> str:
                return "a fake generator"

            def from_text(self, prompt, options=None, on_progress=None):
                return result

            def from_image(self, image, options=None, on_progress=None):
                return result

        return Fake()

    def test_a_generated_shape_becomes_the_open_model(self, tmp_path):
        from modelpop.domain.result import success

        from .test_workspace import box

        generated = success(GeneratedMesh(mesh=box(20), model="trellis", seed=3))
        workspace = self.workspace(self.fake_generator(generated))

        image = tmp_path / "dragon.png"
        image.write_bytes(b"png")
        state = workspace.generate_from_image(image).unwrap()

        assert state.has_model

    def test_the_readiness_report_is_produced_for_it(self):
        """Everything downstream must work on it without knowing where it came
        from. That is the whole point of the port."""
        from modelpop.domain.result import success

        from .test_workspace import box

        generated = success(GeneratedMesh(mesh=box(20), model="trellis"))
        workspace = self.workspace(self.fake_generator(generated))

        state = workspace.adopt(box(20))
        assert state.readiness is not None

    def test_where_the_shape_came_from_is_recorded(self, tmp_path):
        from modelpop.domain.result import success

        from .test_workspace import box

        generated = success(
            GeneratedMesh(mesh=box(20), model="trellis", seed=42, source_image=Path("dragon.png"))
        )
        workspace = self.workspace(self.fake_generator(generated))

        image = tmp_path / "dragon.png"
        image.write_bytes(b"png")
        state = workspace.generate_from_image(image).unwrap()

        assert "trellis" in state.generation_note
        assert "seed 42" in state.generation_note

    def test_without_a_generator_it_says_where_to_read(self, tmp_path):
        image = tmp_path / "dragon.png"
        image.write_bytes(b"png")

        result = self.workspace().generate_from_image(image)
        assert not result.ok
        assert "10-mesh-generation" in result.detail

    def test_the_workspace_reports_whether_it_can(self):
        assert not self.workspace().can_generate_a_mesh
        assert "not wired up" in self.workspace().describe_mesh_generation()

    def test_a_failure_from_the_generator_is_passed_through(self, tmp_path):
        from modelpop.domain.result import failure

        workspace = self.workspace(self.fake_generator(failure("the card caught fire")))
        image = tmp_path / "dragon.png"
        image.write_bytes(b"png")

        result = workspace.generate_from_image(image)
        assert not result.ok
        assert "caught fire" in result.error


class TestTheGeneratorAdapter:
    """Driving trellis.cpp. The binary is absent here, which is the case that
    matters: every message has to name which piece is missing."""

    def generator(self, **kwargs) -> TrellisCliGenerator:
        kwargs.setdefault("binary", None)
        kwargs.setdefault("weights", None)
        return TrellisCliGenerator(TrimeshIO(), **kwargs)

    def installed(self, tmp_path, *, weights: bool = True) -> TrellisCliGenerator:
        """A generator that looks installed, without anything real behind it."""
        binary = tmp_path / "trellis-cli.exe"
        binary.write_text("", encoding="utf-8")

        models = tmp_path / "models"
        models.mkdir(exist_ok=True)
        if weights:
            for name in _WEIGHT_FILES:
                (models / name).write_bytes(b"x")

        return TrellisCliGenerator(
            TrimeshIO(), binary=binary, weights=models, lease=GpuLease.beside(tmp_path)
        )

    def test_it_satisfies_the_port(self):
        assert isinstance(self.generator(), MeshGenerator)

    def test_with_nothing_installed_it_points_at_the_notes(self):
        generator = self.generator()
        assert not generator.is_available()
        assert "not set up" in generator.describe()
        assert "10-mesh-generation" in generator.describe()

    def test_a_binary_with_no_weights_says_which_half_is_missing(self, tmp_path):
        generator = self.installed(tmp_path, weights=False)
        assert not generator.is_available()
        assert "no weights yet" in generator.describe()
        assert "10 GB" in generator.describe()

    def test_a_partial_download_names_the_files_that_are_missing(self, tmp_path):
        """Half a download is a real thing that happens over ten gigabytes."""
        generator = self.installed(tmp_path)
        assert generator.weights is not None
        (generator.weights / "tex_dec.gguf").unlink()

        assert not generator.is_available()
        assert "tex_dec.gguf" in generator.describe()

    def test_a_complete_install_reports_itself_ready(self, tmp_path):
        generator = self.installed(tmp_path)
        assert generator.is_available()
        assert "ready" in generator.describe()
        assert MODEL_NAME in generator.describe()

    def test_a_busy_card_is_reported_as_busy_not_as_missing(self, tmp_path):
        generator = self.installed(tmp_path)
        assert generator.lease is not None

        with generator.lease.held():
            assert not generator.is_available()
            assert "already running" in generator.describe()

    def test_a_missing_image_is_caught_before_anything_starts(self, tmp_path):
        result = self.installed(tmp_path).from_image(tmp_path / "absent.png")
        assert not result.ok
        assert "does not exist" in result.error

    def test_words_alone_are_refused_honestly(self, tmp_path):
        """A dragon that is not the dragon you asked for is worse than a message."""
        result = self.installed(tmp_path).from_text("an MSI dragon")
        assert not result.ok
        assert "picture" in result.detail

    def test_an_empty_description_is_refused(self):
        assert not self.generator().from_text("   ").ok


class TestTheCommandItBuilds:
    """Getting this wrong produces a shape, just not the right one - so it is
    asserted on directly rather than inferred from a result."""

    def generator(self, tmp_path) -> TrellisCliGenerator:
        models = tmp_path / "models"
        models.mkdir(exist_ok=True)
        return TrellisCliGenerator(TrimeshIO(), binary=tmp_path / "trellis-cli.exe", weights=models)

    def command(self, tmp_path, options: GenerationOptions) -> list[str]:
        return self.generator(tmp_path)._command(tmp_path / "in.png", tmp_path / "out.glb", options)

    def test_arguments_are_a_list_not_a_joined_string(self, tmp_path):
        """The rule the slicer adapter learned the hard way: a space in a path
        becomes two arguments otherwise."""
        command = self.command(tmp_path, GenerationOptions())
        assert isinstance(command, list)
        assert all(isinstance(part, str) for part in command)

    def test_the_detail_setting_chooses_a_resolution(self, tmp_path):
        draft = self.command(tmp_path, GenerationOptions(detail=Detail.DRAFT))
        standard = self.command(tmp_path, GenerationOptions(detail=Detail.STANDARD))

        assert "512" in draft
        assert "1024" in standard

    def test_the_highest_setting_does_not_ask_for_an_unproven_resolution(self, tmp_path):
        """The project only claims 1024 fits a 16 GB card."""
        fine = self.command(tmp_path, GenerationOptions(detail=Detail.FINE))
        assert "1536" not in fine

    def test_a_stated_seed_is_passed_so_a_run_can_be_repeated(self, tmp_path):
        command = self.command(tmp_path, GenerationOptions(seed=1234))
        assert "--seed" in command
        assert "1234" in command

    def test_no_seed_means_no_seed_argument(self, tmp_path):
        assert "--seed" not in self.command(tmp_path, GenerationOptions(seed=0))

    def test_keeping_the_background_uses_the_simple_keyer(self, tmp_path):
        """Also the way round the open bug in the smart one."""
        command = self.command(tmp_path, GenerationOptions(remove_background=False))
        assert "--bg-removal" in command
        assert "threshold" in command

    def test_the_weights_directory_is_named(self, tmp_path):
        assert "--models" in self.command(tmp_path, GenerationOptions())

    def test_asking_for_fine_detail_says_it_was_capped(self, tmp_path):
        """Quietly giving less than was asked for is worse than saying so."""
        notes = self.generator(tmp_path)._notes(GenerationOptions(detail=Detail.FINE))
        assert notes
        assert "1024" in notes[0]

    def test_an_ordinary_run_has_nothing_to_report(self, tmp_path):
        assert self.generator(tmp_path)._notes(GenerationOptions()) == ()


class TestReadingItsProgress:
    """It writes its stage as it goes, which the Bambu CLI does not."""

    def report(self, line: str) -> list[tuple[float, str]]:
        seen: list[tuple[float, str]] = []
        _report(line, lambda fraction, message: seen.append((fraction, message)))
        return seen

    def test_a_stage_line_becomes_a_fraction_and_a_phrase(self):
        seen = self.report("[3/7] shape flow 1024")
        assert len(seen) == 1
        fraction, message = seen[0]
        assert fraction == pytest.approx(3 / 7)
        assert message == "shape flow 1024"

    def test_the_last_stage_is_complete_rather_than_over_one(self):
        assert self.report("[7/7] write")[0][0] == pytest.approx(1.0)

    def test_an_ordinary_line_reports_nothing(self):
        assert self.report("loading weights from models/ss_flow.gguf") == []

    def test_a_malformed_stage_line_is_ignored_rather_than_fatal(self):
        assert self.report("[x/y] something") == []

    def test_a_zero_total_does_not_divide_by_zero(self):
        assert self.report("[0/0] nothing") == []


class TestWhereThingsAre:
    def test_an_explicit_binary_setting_wins(self, monkeypatch, tmp_path):
        stated = tmp_path / "trellis-cli.exe"
        stated.write_text("", encoding="utf-8")
        monkeypatch.setenv("MODELPOP_TRELLIS_CLI", str(stated))

        assert find_trellis_cli() == stated

    def test_a_setting_pointing_at_nothing_is_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MODELPOP_TRELLIS_CLI", str(tmp_path / "gone.exe"))
        assert find_trellis_cli() is None

    def test_an_explicit_weights_setting_wins(self, monkeypatch, tmp_path):
        models = tmp_path / "models"
        models.mkdir(exist_ok=True)
        monkeypatch.setenv("MODELPOP_TRELLIS_MODELS", str(models))

        assert find_weights() == models

    def test_a_weights_setting_pointing_at_a_file_is_ignored(self, monkeypatch, tmp_path):
        stray = tmp_path / "not-a-directory"
        stray.write_text("", encoding="utf-8")
        monkeypatch.setenv("MODELPOP_TRELLIS_MODELS", str(stray))

        assert find_weights() is None
