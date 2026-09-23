"""Making a mesh from a picture, by driving trellis.cpp.

A native binary, run as a subprocess - structurally the same adapter as the
Bambu Studio slicer (ADR-0006), and for the same reason: the capability lives
outside the process, so a crash, a hang or a missing install is a message rather
than a dead application.

**Why a binary and not Python**, in short: nothing in this class installs on
Windows without compiling CUDA extensions, and the Python TRELLIS.2 needs 24 GB
of video memory against this machine's 16. `trellis.cpp` ships prebuilt Windows
CUDA binaries with quantised weights and is engineered to fit 16 GB. The whole
measurement is in ``docs/research/spike-image-to-3d.md``.

Two things about it shape this adapter:

**It reports its stages on stdout** - ``[3/7] shape flow ...`` - so a progress
bar comes free, unlike the Bambu CLI which says nothing at all.

**The first call is slow** because it loads ten gigabytes of weights. A timeout
tuned for the second call would kill the first one every time.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from modelpop.application.mesh_generation_ports import (
    Background,
    Detail,
    GeneratedMesh,
    GenerationOptions,
)
from modelpop.domain.mesh import Mesh
from modelpop.domain.result import Result, failure, success
from modelpop.domain.units import Length
from modelpop.generation.gpu_lease import GpuBusyError, GpuLease
from modelpop.paths import app_data_dir

if TYPE_CHECKING:
    from modelpop.application.mesh_generation_ports import Progress
    from modelpop.application.ports import MeshIO

__all__ = ["TrellisCliGenerator", "find_trellis_cli"]

MODEL_NAME = "TRELLIS.2 via trellis.cpp"

_ENVIRONMENT_VARIABLE = "MODELPOP_TRELLIS_CLI"
_WEIGHTS_VARIABLE = "MODELPOP_TRELLIS_MODELS"

# Resolution rather than step count is what the binary exposes, and it is the
# knob that actually trades time for detail. 1536 is left out on purpose: the
# project's own notes only claim 1024 fits a 16 GB card.
_RESOLUTIONS = {Detail.DRAFT: 512, Detail.STANDARD: 1024, Detail.FINE: 1024}

# Ten gigabytes of weights load on the first call. A limit tuned for the second
# would kill the first every time.
_FIRST_CALL_GRACE = 600.0

# The binary writes "[3/7] shape flow" as it goes.
_STAGE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]\s*(.+)")

_WEIGHT_FILES = (
    "birefnet.gguf",
    "dinov3.gguf",
    "ss_flow.gguf",
    "ss_dec.gguf",
    "shape_flow_512.gguf",
    "shape_flow_1024.gguf",
    "shape_dec.gguf",
    "tex_flow_512.gguf",
    "tex_flow_1024.gguf",
    "tex_dec.gguf",
)


def find_trellis_cli() -> Path | None:
    """Where the generator binary is, if it is anywhere.

    An explicit setting wins, then the conventional place beside the
    application's own data, then whatever is on PATH. Returning ``None`` is the
    ordinary answer on a machine where nothing has been installed.
    """
    stated = os.environ.get(_ENVIRONMENT_VARIABLE)
    if stated:
        candidate = Path(stated)
        return candidate if candidate.exists() else None

    name = "trellis-cli.exe" if os.name == "nt" else "trellis-cli"
    for relative in (f"trellis/runtime/{name}", f"trellis/{name}"):
        candidate = app_data_dir() / relative
        if candidate.exists():
            return candidate

    on_path = shutil.which("trellis-cli")
    return Path(on_path) if on_path else None


def find_weights() -> Path | None:
    """Where the model weights are, if they have been downloaded."""
    stated = os.environ.get(_WEIGHTS_VARIABLE)
    if stated:
        candidate = Path(stated)
        return candidate if candidate.is_dir() else None

    for base in (
        app_data_dir() / "trellis" / "models",
        Path(os.environ.get("LOCALAPPDATA", "")) / "trellis-studio" / "models",
    ):
        if base.is_dir() and any((base / name).exists() for name in _WEIGHT_FILES):
            return base
    return None


@dataclass
class TrellisCliGenerator:
    """Satisfies the ``MeshGenerator`` port by driving the trellis.cpp binary."""

    mesh_io: MeshIO
    binary: Path | None = field(default_factory=find_trellis_cli)
    weights: Path | None = field(default_factory=find_weights)
    lease: GpuLease | None = None

    def __post_init__(self) -> None:
        """Give the lease a home if one was not supplied."""
        if self.lease is None:
            self.lease = GpuLease.beside(app_data_dir())

    # ----------------------------------------------------------- availability

    @property
    def missing_weights(self) -> tuple[str, ...]:
        """Which weight files are absent, for a message that names them."""
        if self.weights is None:
            return _WEIGHT_FILES
        return tuple(name for name in _WEIGHT_FILES if not (self.weights / name).exists())

    def is_available(self) -> bool:
        """Whether a generation could start right now."""
        lease = self.lease
        return (
            self.binary is not None
            and not self.missing_weights
            and (lease is None or lease.is_free)
        )

    def describe(self) -> str:
        """The state of the generator, specifically enough to act on.

        Each state needs a different thing from the user, so each gets its own
        sentence rather than one "unavailable".
        """
        if self.binary is None:
            return (
                "Making a model from a picture is not set up. It needs the "
                "trellis.cpp binary - see docs/10-mesh-generation.md."
            )

        missing = self.missing_weights
        if missing:
            if len(missing) == len(_WEIGHT_FILES):
                return (
                    f"{MODEL_NAME} is installed but has no weights yet. They are "
                    "about 10 GB - see docs/10-mesh-generation.md."
                )
            return (
                f"{MODEL_NAME} is missing {len(missing)} weight file(s): "
                f"{', '.join(missing[:3])}{'...' if len(missing) > 3 else ''}."
            )

        lease = self.lease
        if lease is not None and not lease.is_free:
            return lease.describe()
        return f"{MODEL_NAME}, ready."

    # -------------------------------------------------------------- generating

    def from_text(
        self,
        prompt: str,
        options: GenerationOptions | None = None,
        on_progress: Progress | None = None,
    ) -> Result[GeneratedMesh]:
        """Make a mesh from a description.

        Refused honestly. This backend is image-to-3D, and a dragon that is not
        the dragon you asked for is worse than a message saying so.
        """
        if not prompt.strip():
            return failure("Nothing was described")
        return failure(
            "Making a shape from words alone is not wired up yet",
            "This generator works from a picture. Supply an image, or describe a "
            "mechanical part instead - that path does work.",
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
        if not self.is_available():
            return failure("Making a model from a picture is not available", self.describe())

        settings = options or GenerationOptions()
        lease = self.lease

        with tempfile.TemporaryDirectory(prefix="modelpop-generate-") as scratch:
            output = Path(scratch) / "generated.glb"
            try:
                if lease is None:
                    ran = self._run(image, output, settings, on_progress)
                else:
                    with lease.held():
                        ran = self._run(image, output, settings, on_progress)
            except GpuBusyError as busy:
                return failure("The graphics card is busy", str(busy))

            if not ran.ok:
                return ran  # type: ignore[return-value]

            if not output.exists():
                return failure(
                    "The generator finished without producing a model",
                    "It reported no error, which usually means it ran out of memory.",
                )

            loaded = self.mesh_io.load(output)
            if not loaded.ok:
                return failure(
                    "The generated model could not be read",
                    "It wrote a file ModelPop could not open.",
                )

            mesh, scale_note = _given_a_scale(
                loaded.unwrap(), settings.size, measured=settings.size_was_measured
            )
            return success(
                GeneratedMesh(
                    mesh=mesh,
                    model=MODEL_NAME,
                    seed=settings.seed,
                    source_image=image,
                    textured_path=_kept(output),
                    seconds=ran.unwrap(),
                    notes=(*self._notes(settings), *scale_note),
                )
            )

    # --------------------------------------------------------------- internal

    def _command(self, image: Path, output: Path, settings: GenerationOptions) -> list[str]:
        """The binary and its arguments, as a list.

        A list rather than a joined string, which is the rule the slicer adapter
        learned the hard way: a space in a path becomes two arguments otherwise.
        """
        assert self.binary is not None
        command = [
            str(self.binary),
            str(image),
            str(output),
            "--res",
            str(_RESOLUTIONS[settings.detail]),
        ]
        if self.weights is not None:
            command += ["--models", str(self.weights)]
        if settings.seed:
            command += ["--seed", str(settings.seed)]
        if settings.background is Background.SIMPLE:
            command += ["--bg-removal", "threshold"]
        if settings.require_gpu:
            # Without this it silently falls back to the processor, where a
            # single generation takes hours rather than failing.
            command.append("--require-gpu")
        return command

    def _run(
        self,
        image: Path,
        output: Path,
        settings: GenerationOptions,
        on_progress: Progress | None,
    ) -> Result[float]:
        """Start the binary, follow its stages, and wait for it."""
        command = self._command(image, output, settings)
        started = time.perf_counter()

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            return failure("The generator could not be started", str(error))

        tail: list[str] = []
        try:
            for line in process.stdout or ():
                tail.append(line.rstrip())
                del tail[:-30]
                if on_progress is not None:
                    _report(line, on_progress)

            process.wait(timeout=settings.timeout_seconds + _FIRST_CALL_GRACE)
        except subprocess.TimeoutExpired:
            process.kill()
            return failure(
                "Generation took too long and was stopped",
                f"It ran for over {(settings.timeout_seconds + _FIRST_CALL_GRACE) / 60:.0f} "
                "minutes. The first run is slow because it loads ten gigabytes of weights.",
            )
        finally:
            if process.poll() is None:
                process.kill()

        if process.returncode != 0:
            return failure(
                "The generator failed",
                "\n".join(tail[-6:]) or f"it exited with code {process.returncode}",
            )

        return success(time.perf_counter() - started)

    def _notes(self, settings: GenerationOptions) -> tuple[str, ...]:
        """Anything the user should know about what was produced."""
        notes: list[str] = []
        if settings.detail is Detail.FINE:
            notes.append(
                "Asked for fine detail, which this generator caps at 1024 - the higher "
                "setting is not proven on a 16 GB card."
            )
        if settings.background is Background.SIMPLE:
            notes.append(
                "Used the quick background keyer, which can leave holes where a shiny surface was."
            )
        # The binary simplifies to 300k faces at 1024 and 150k at 512 by
        # default, which is already a sane print budget. Passing our own would
        # be a second opinion with no better information behind it.
        return tuple(notes)


# Below this the model came out of the generator's normalised box rather than
# from anything measured, so its size means nothing and is replaced.
_UNSCALED_MM = 10.0


def _kept(output: Path) -> Path | None:
    """Copy the generator's textured file somewhere it survives the job directory.

    The mesh is read out and the scratch directory is swept away, which is
    right for everything except the texture: detail rescue bakes that colour
    into the surface, and it is the one thing the domain mesh cannot carry.
    A copy costs a couple of megabytes and is the difference between the
    feature being possible and not.
    """
    if not output.is_file():
        return None
    destination = Path(tempfile.gettempdir()) / f"modelpop-generated-{os.getpid()}-{output.name}"
    try:
        shutil.copy2(output, destination)
    except OSError:
        return None
    return destination


def _given_a_scale(
    mesh: Mesh, size: Length, *, measured: bool = False
) -> tuple[Mesh, tuple[str, ...]]:
    """Resize a generated model to something printable, and say where the size came from.

    The generator works in a normalised box and returns a model one unit
    across. Read as millimetres that is a grain of sand, so it is scaled.

    The note is the part that matters. A size measured against a ruler in the
    shot can be checked with calipers; a size the app picked cannot, and a
    model that says nothing looks exactly like one that was measured. So the
    two say different things, and neither is silent.

    A model that already has a plausible size is left alone, so a backend that
    one day returns real units is not scaled twice.
    """
    largest = mesh.bounds.largest_dimension.millimetres
    if largest <= 0:
        return mesh, ("The generator produced a model with no size at all.",)
    if largest > _UNSCALED_MM:
        return mesh, ()

    note = (
        f"Scaled to {size.format()} at its largest, measured against a reference in the photograph."
        if measured
        else f"A picture has no scale, so this was made {size.format()} at its "
        "largest. Use Resize to set the real size, or measure against a ruler "
        "in the shot next time."
    )
    return mesh.scaled_to_fit(size), (note,)


def _report(line: str, on_progress: Progress) -> None:
    """Turn one of the binary's stage lines into a progress report."""
    match = _STAGE.search(line)
    if match is None:
        return
    try:
        step, total = int(match.group(1)), int(match.group(2))
    except ValueError:
        return
    if total <= 0:
        return
    on_progress(min(step / total, 1.0), match.group(3).strip())
