"""Driving the generation environment from ModelPop.

The same shape as the CAD kernel adapter, for the same reasons: a separate
process, a JSON protocol, a timeout, and every failure arriving as a ``Result``
rather than an exception. The difference is what is on the other end - PyTorch,
CUDA and several gigabytes of weights, none of which ModelPop itself may depend
on.

**Everything here assumes the environment might not exist.** That is the normal
state on a fresh machine, and it is not an error: `is_available` says no, the
button is disabled, and Settings explains what to install. A feature that is not
set up should look unavailable, not broken.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from modelpop.application.mesh_generation_ports import GeneratedMesh, GenerationOptions
from modelpop.domain.result import Result, failure, success
from modelpop.generation.gpu_lease import GpuBusyError, GpuLease

if TYPE_CHECKING:
    from modelpop.application.mesh_generation_ports import Progress
    from modelpop.application.ports import MeshIO

__all__ = ["ExternalMeshGenerator", "GenerationEnvironment", "find_generation_python"]

# The worker is handed to another interpreter as a file path, so it has to be
# findable on disk rather than imported.
_WORKER = Path(__file__).with_name("mesh_worker.py")

# Probing starts a Python and imports torch. On a cold cache that is slow, and
# it happens once at start-up, so the limit is generous.
_PROBE_TIMEOUT = 120.0

_ENVIRONMENT_VARIABLE = "MODELPOP_GENERATION_PYTHON"


def find_generation_python() -> Path | None:
    """Where the generation environment's Python is, if there is one.

    An explicit environment variable wins, then the conventional location
    beside the application's own data. Returning ``None`` is an ordinary answer
    on a machine where nothing has been installed yet.
    """
    stated = os.environ.get(_ENVIRONMENT_VARIABLE)
    if stated:
        candidate = Path(stated)
        return candidate if candidate.exists() else None

    from modelpop.repositories.acceptance import app_data_dir

    for relative in ("generation/Scripts/python.exe", "generation/bin/python"):
        candidate = app_data_dir() / relative
        if candidate.exists():
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class GenerationEnvironment:
    """What the separate environment turned out to contain."""

    python: Path | None = None
    torch_version: str = ""
    cuda: bool = False
    device: str = ""
    vram_gb: float = 0.0
    backends: tuple[str, ...] = ()
    problem: str = ""

    @property
    def is_ready(self) -> bool:
        """Whether a generation could actually run."""
        return bool(self.python) and self.cuda and bool(self.backends)

    def describe(self) -> str:
        """What state this is in, specifically enough to act on.

        "Not installed" and "no GPU free" need different answers from the user,
        so they get different sentences.
        """
        if self.python is None:
            return (
                "Mesh generation is not set up. See docs/10-mesh-generation.md - "
                "it needs its own Python environment with PyTorch."
            )
        if self.problem:
            return f"The generation environment could not be read: {self.problem}"
        if not self.torch_version:
            return "The generation environment has no PyTorch installed."
        if not self.cuda:
            return (
                f"PyTorch {self.torch_version} is installed but cannot see the graphics "
                "card, so generation would take hours on the processor."
            )
        if not self.backends:
            return (
                f"PyTorch {self.torch_version} on {self.device or 'the GPU'} is ready, "
                "but no generator is installed yet."
            )
        return (
            f"{', '.join(self.backends)} on {self.device or 'the GPU'}"
            f"{f' ({self.vram_gb:g} GB)' if self.vram_gb else ''}."
        )


@dataclass
class ExternalMeshGenerator:
    """Satisfies the ``MeshGenerator`` port by driving another interpreter."""

    mesh_io: MeshIO
    python: Path | None = field(default_factory=find_generation_python)
    lease: GpuLease | None = None
    weights: str = ""
    """Override which checkpoint to load. Empty means the backend's default."""

    _environment: GenerationEnvironment | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Give the lease a home if one was not supplied."""
        if self.lease is None:
            from modelpop.repositories.acceptance import app_data_dir

            self.lease = GpuLease.beside(app_data_dir())

    # ----------------------------------------------------------- availability

    def environment(self, *, refresh: bool = False) -> GenerationEnvironment:
        """What the generation environment contains.

        Cached, because probing starts an interpreter and imports PyTorch.
        ``refresh`` is for after the user has installed something.
        """
        if self._environment is not None and not refresh:
            return self._environment

        if self.python is None:
            self._environment = GenerationEnvironment()
            return self._environment

        self._environment = self._probe(self.python)
        return self._environment

    def is_available(self) -> bool:
        """Whether a generation could start right now."""
        lease = self.lease
        return self.environment().is_ready and (lease is None or lease.is_free)

    def describe(self) -> str:
        """The state of the generator, for Settings."""
        environment = self.environment()
        if not environment.is_ready:
            return environment.describe()
        lease = self.lease
        if lease is not None and not lease.is_free:
            return lease.describe()
        return environment.describe()

    # -------------------------------------------------------------- generating

    def from_text(
        self,
        prompt: str,
        options: GenerationOptions | None = None,
        on_progress: Progress | None = None,
    ) -> Result[GeneratedMesh]:
        """Make a mesh from a description.

        The installed backends are image-to-3D, so a description has to become
        a picture first. Until that path exists this says so plainly rather than
        producing something unrelated to what was asked for.
        """
        if not prompt.strip():
            return failure("Nothing was described")
        return failure(
            "Making a shape from words alone is not wired up yet",
            "The installed generators work from a picture. Supply an image, or "
            "describe a mechanical part instead - that path does work.",
        )

    def from_image(
        self,
        image: Path,
        options: GenerationOptions | None = None,
        on_progress: Progress | None = None,
    ) -> Result[GeneratedMesh]:
        """Make a mesh from a single photo or drawing."""
        if not image.exists():
            return failure("That image does not exist", str(image))

        environment = self.environment()
        if not environment.is_ready:
            return failure("Mesh generation is not available", environment.describe())

        settings = options or GenerationOptions()
        return self._run(
            {
                "image": str(image),
                "remove_background": settings.remove_background,
            },
            settings,
            on_progress,
            source_image=image,
        )

    # --------------------------------------------------------------- internal

    def _run(
        self,
        request: dict[str, Any],
        options: GenerationOptions,
        on_progress: Progress | None,
        source_image: Path | None = None,
        prompt: str = "",
    ) -> Result[GeneratedMesh]:
        """Start the worker, follow its progress, and load what it made."""
        lease = self.lease
        assert self.python is not None  # is_ready implies it

        with tempfile.TemporaryDirectory(prefix="modelpop-generate-") as scratch:
            output = Path(scratch) / "generated.glb"
            payload = {
                **request,
                "output": str(output),
                "seed": options.seed,
                "detail": options.detail.value,
                "target_triangles": options.target_triangles,
                "weights": self.weights,
            }

            try:
                if lease is None:
                    finished = self._talk(payload, options.timeout_seconds, on_progress)
                else:
                    with lease.held():
                        finished = self._talk(payload, options.timeout_seconds, on_progress)
            except GpuBusyError as busy:
                return failure("The graphics card is busy", str(busy))

            if not finished.ok:
                return finished  # type: ignore[return-value]

            report = finished.unwrap()
            loaded = self.mesh_io.load(output)
            if not loaded.ok:
                return failure(
                    "The generated model could not be read",
                    "The generator wrote a file ModelPop could not open.",
                )

            return success(
                GeneratedMesh(
                    mesh=loaded.unwrap(),
                    model=", ".join(self.environment().backends),
                    seed=int(report.get("seed") or 0),
                    prompt=prompt,
                    source_image=source_image,
                    seconds=float(report.get("seconds") or 0.0),
                    notes=tuple(report.get("notes") or ()),
                )
            )

    def _talk(
        self, payload: dict[str, Any], timeout: float, on_progress: Progress | None
    ) -> Result[dict[str, Any]]:
        """Run the worker and read its lines until it finishes."""
        assert self.python is not None
        command = [str(self.python), str(_WORKER), "--request", json.dumps(payload)]

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            return failure("The generator could not be started", str(error))

        finished: dict[str, Any] | None = None
        problem: dict[str, Any] | None = None

        try:
            for line in process.stdout or ():
                message = _read_line(line)
                if message is None:
                    continue
                phase = message.get("phase")
                if phase == "progress" and on_progress is not None:
                    on_progress(
                        float(message.get("fraction", 0.0)), str(message.get("message", ""))
                    )
                elif phase == "done":
                    finished = message
                elif phase == "failed":
                    problem = message

            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            return failure(
                "Generation took too long and was stopped",
                f"It ran for over {timeout / 60:.0f} minutes.",
            )
        finally:
            if process.poll() is None:
                process.kill()

        if problem is not None:
            return failure(
                str(problem.get("error", "Generation failed")), str(problem.get("detail", ""))
            )
        if finished is None:
            stderr = (process.stderr.read() if process.stderr else "")[-400:]
            return failure("The generator stopped without producing anything", stderr.strip())
        return success(finished)

    def _probe(self, python: Path) -> GenerationEnvironment:
        """Ask the environment what it has."""
        try:
            completed = subprocess.run(
                [str(python), str(_WORKER), "--probe", "--request", "{}"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return GenerationEnvironment(python=python, problem=str(error))

        for line in completed.stdout.splitlines():
            message = _read_line(line)
            if message is None or message.get("phase") != "probe":
                continue
            return GenerationEnvironment(
                python=python,
                torch_version=str(message.get("torch") or ""),
                cuda=bool(message.get("cuda")),
                device=str(message.get("device") or ""),
                vram_gb=float(message.get("vram_gb") or 0.0),
                backends=tuple(message.get("backends") or ()),
                problem=str(message.get("torch_error") or ""),
            )

        return GenerationEnvironment(
            python=python,
            problem=(completed.stderr or "it said nothing").strip()[-300:],
        )


def _read_line(line: str) -> dict[str, Any] | None:
    """One JSON line from the worker, or ``None`` if it is not one.

    A package printing a warning to stdout is normal in this world, so anything
    unparseable is skipped rather than treated as a failure.
    """
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
