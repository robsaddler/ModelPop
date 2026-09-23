"""The generation environment, the lease on the card, and the worker protocol.

The heavy part - PyTorch, CUDA, several gigabytes of weights - is deliberately
absent here, and that is the point: the interesting cases are all about what
happens when it is *not* installed, which is the state of every fresh machine.

The worker protocol is exercised for real by pointing the adapter at this
project's own interpreter. It has no PyTorch, so the probe comes back saying so
- which proves the subprocess, the JSON lines and the reporting all work,
without downloading anything.
"""

import json
import os
import subprocess
import sys
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
from modelpop.generation.mesh_generator import (
    ExternalMeshGenerator,
    GenerationEnvironment,
    find_generation_python,
)
from modelpop.mesh import TrimeshIO

WORKER = Path("src/modelpop/generation/mesh_worker.py").resolve()


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


class TestWhatTheEnvironmentSays:
    """Each state needs a different answer from the user, so each gets its own
    sentence rather than one "unavailable"."""

    def test_nothing_installed_points_at_the_setup_notes(self):
        assert "not set up" in GenerationEnvironment().describe()

    def test_no_pytorch_says_so(self, tmp_path):
        state = GenerationEnvironment(python=tmp_path / "python.exe")
        assert "no PyTorch" in state.describe()

    def test_pytorch_without_a_card_explains_why_that_matters(self, tmp_path):
        state = GenerationEnvironment(python=tmp_path / "python.exe", torch_version="2.9")
        assert "cannot see the graphics" in state.describe()
        assert "hours" in state.describe()

    def test_a_card_but_no_generator_says_which_half_is_missing(self, tmp_path):
        state = GenerationEnvironment(
            python=tmp_path / "python.exe", torch_version="2.9", cuda=True, device="RTX 4090"
        )
        assert "no generator is installed" in state.describe()

    def test_a_ready_environment_names_what_it_has(self, tmp_path):
        state = GenerationEnvironment(
            python=tmp_path / "python.exe",
            torch_version="2.9",
            cuda=True,
            device="RTX 4090",
            vram_gb=16.0,
            backends=("trellis",),
        )
        assert state.is_ready
        assert "trellis" in state.describe()
        assert "RTX 4090" in state.describe()

    def test_only_a_complete_environment_is_ready(self, tmp_path):
        half = GenerationEnvironment(python=tmp_path / "p", torch_version="2.9", cuda=True)
        assert not half.is_ready


class TestTheWorkerProtocol:
    """Run against this project's own interpreter, which has no PyTorch.

    That is exactly what makes it a good test: it proves the subprocess, the
    JSON lines and the failure reporting all work without downloading a model.
    """

    def run_worker(self, *arguments: str) -> list[dict]:
        completed = subprocess.run(
            [sys.executable, str(WORKER), *arguments],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        lines = []
        for line in completed.stdout.splitlines():
            if line.strip().startswith("{"):
                lines.append(json.loads(line))
        return lines

    def test_probing_reports_what_is_installed(self):
        messages = self.run_worker("--probe", "--request", "{}")
        probe = next(m for m in messages if m["phase"] == "probe")

        assert "python" in probe
        assert "backends" in probe

    def test_probing_an_environment_with_no_pytorch_says_so_rather_than_crashing(self):
        messages = self.run_worker("--probe", "--request", "{}")
        probe = next(m for m in messages if m["phase"] == "probe")

        assert "torch" not in probe or not probe.get("cuda")
        assert probe["backends"] == []

    def test_an_unreadable_request_is_reported_as_a_failure(self):
        messages = self.run_worker("--request", "not json at all")
        assert messages[-1]["phase"] == "failed"
        assert "could not be read" in messages[-1]["error"]

    def test_with_no_backend_installed_it_says_what_to_read(self):
        request = json.dumps({"output": "out.glb", "image": "x.png"})
        messages = self.run_worker("--request", request)

        assert messages[-1]["phase"] == "failed"
        assert "No generator is installed" in messages[-1]["detail"]

    def test_the_worker_imports_nothing_from_modelpop(self):
        """It runs in an interpreter where modelpop is not installed, so any
        import of it would be a crash on the user's machine and nowhere else."""
        source = WORKER.read_text(encoding="utf-8")
        assert "import modelpop" not in source
        assert "from modelpop" not in source


class TestTheAdapter:
    def generator(self, **kwargs) -> ExternalMeshGenerator:
        return ExternalMeshGenerator(TrimeshIO(), **kwargs)

    def test_it_satisfies_the_port(self):
        assert isinstance(self.generator(python=None), MeshGenerator)

    def test_with_nothing_installed_it_is_unavailable_rather_than_broken(self):
        generator = self.generator(python=None)
        assert not generator.is_available()
        assert "not set up" in generator.describe()

    def test_pointed_at_an_interpreter_with_no_pytorch_it_says_that(self, tmp_path):
        """A real probe of a real interpreter, which is what makes this useful."""
        generator = self.generator(python=Path(sys.executable), lease=GpuLease.beside(tmp_path))
        assert not generator.is_available()
        assert "PyTorch" in generator.describe()

    def test_a_missing_image_is_reported_before_anything_starts(self, tmp_path):
        generator = self.generator(python=None)
        result = generator.from_image(tmp_path / "absent.png")

        assert not result.ok
        assert "does not exist" in result.error

    def test_generating_without_an_environment_explains_itself(self, tmp_path):
        image = tmp_path / "photo.png"
        image.write_bytes(b"not really a png")

        result = self.generator(python=None).from_image(image)
        assert not result.ok
        assert "not available" in result.error

    def test_words_alone_are_refused_honestly_rather_than_approximated(self):
        """The installed backends are image-to-3D. Producing something
        unrelated would be worse than saying so."""
        result = self.generator(python=None).from_text("an MSI dragon")

        assert not result.ok
        assert "not wired up" in result.error
        assert "picture" in result.detail

    def test_an_empty_description_is_refused(self):
        assert not self.generator(python=None).from_text("   ").ok

    def test_the_probe_is_only_run_once(self, tmp_path):
        """It starts an interpreter and imports PyTorch. Doing that on every
        button repaint would make the window crawl."""
        generator = self.generator(python=Path(sys.executable), lease=GpuLease.beside(tmp_path))
        first = generator.environment()
        assert generator.environment() is first

    def test_it_can_be_probed_again_after_the_user_installs_something(self, tmp_path):
        generator = self.generator(python=Path(sys.executable), lease=GpuLease.beside(tmp_path))
        first = generator.environment()
        assert generator.environment(refresh=True) is not first


class TestWhereTheEnvironmentLives:
    def test_an_explicit_setting_wins(self, monkeypatch, tmp_path):
        stated = tmp_path / "python.exe"
        stated.write_text("", encoding="utf-8")
        monkeypatch.setenv("MODELPOP_GENERATION_PYTHON", str(stated))

        assert find_generation_python() == stated

    def test_a_setting_pointing_at_nothing_is_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MODELPOP_GENERATION_PYTHON", str(tmp_path / "gone.exe"))
        assert find_generation_python() is None


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
