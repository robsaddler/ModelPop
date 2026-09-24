"""Measuring a shape from photographs, with COLMAP and OpenMVS.

Seven subprocesses across two programs, in a scratch directory that is swept
away afterwards. Structurally the same adapter as the slicer and the mesh
generator, and for the same reasons: neither tool is a Python library, both are
large, and both must be able to fail, hang or be missing without taking the
application with them.

Everything here was written against the measured behaviour in
`docs/research/spike-photogrammetry.md` rather than against the documentation,
because on this pair they disagree in three places that matter:

* **COLMAP's mapper writes to a numbered subdirectory** and can write *no*
  directory at all while still exiting zero. That silent nothing is the real
  failure mode of photogrammetry, so it is checked for by name.
* **COLMAP 4.2 renamed its options.** It is ``--FeatureExtraction.use_gpu``;
  the name in every tutorial online is rejected outright.
* **OpenMVS says nothing on stdout.** It writes a timestamped log into the
  working directory, so a failure can only be diagnosed by reading that file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from modelpop.application.gpu_ports import GpuBusyError as SharedGpuBusyError
from modelpop.application.reconstruction_ports import (
    Quality,
    Reconstruction,
    ReconstructionOptions,
    Stage,
    progress_through,
)
from modelpop.domain.photo_set import PhotoSet
from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from modelpop.application.gpu_ports import GpuLease
    from modelpop.application.ports import MeshIO
    from modelpop.application.reconstruction_ports import Progress
    from modelpop.domain.mesh import Mesh

__all__ = [
    "ColmapOpenMvsReconstructor",
    "find_colmap",
    "find_openmvs",
    "newest_log",
    "pick_model_directory",
]

COLMAP_VARIABLE = "MODELPOP_COLMAP"
OPENMVS_VARIABLE = "MODELPOP_OPENMVS"

# Where the two were installed on this machine. Not a guess: neither tool is in
# winget, so both are unzipped GitHub builds and there is no registry entry or
# installer path to look up. See the memory note and ADR-0012.
_COLMAP_GUESSES = (
    Path(r"C:\Tools\COLMAP\bin\colmap.exe"),
    Path(r"C:\Program Files\COLMAP\bin\colmap.exe"),
)
_OPENMVS_GUESSES = (
    Path(r"C:\Tools\OpenMVS"),
    Path(r"C:\Program Files\OpenMVS"),
)

# The OpenMVS tools this drives. Named so a half-extracted install is reported
# by what is missing rather than as a generic failure.
_OPENMVS_TOOLS = ("InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh")

# How far to downscale before densifying. This is the one setting that decides
# whether a run takes two minutes or two hours; higher numbers are coarser.
_RESOLUTION_LEVEL = {Quality.DRAFT: 3, Quality.NORMAL: 2, Quality.FINE: 1}

# Both the message and the test for it, in one place: mapping is judged by
# its output rather than its exit code, and a *timeout* is the one failure
# that must escape that rule - see `_run`.
_TOO_LONG = "took too long and was stopped"

# A third of a million vertices arrives by default and stutters in the viewport.
# Matches the workspace's own display budget.
_DISPLAY_BUDGET = 300_000


def _executable(name: str) -> str:
    """The platform's spelling of a program name."""
    return f"{name}.exe" if os.name == "nt" else name


def find_colmap() -> Path | None:
    """Where COLMAP is, if it is anywhere.

    An explicit setting wins, then the places it is actually installed, then
    whatever is on PATH. ``None`` is the ordinary answer on a machine that has
    never had it.
    """
    stated = os.environ.get(COLMAP_VARIABLE)
    if stated:
        candidate = Path(stated)
        return candidate if candidate.is_file() else None

    for candidate in _COLMAP_GUESSES:
        if candidate.is_file():
            return candidate

    on_path = shutil.which("colmap")
    return Path(on_path) if on_path else None


def find_openmvs() -> Path | None:
    """The directory holding the OpenMVS tools, if it is anywhere.

    A directory rather than one program, because seven tools ship together and
    the pipeline uses three of them.
    """
    stated = os.environ.get(OPENMVS_VARIABLE)
    if stated:
        candidate = Path(stated)
        return candidate if candidate.is_dir() else None

    for candidate in _OPENMVS_GUESSES:
        if (candidate / _executable("DensifyPointCloud")).is_file():
            return candidate

    on_path = shutil.which("DensifyPointCloud")
    return Path(on_path).parent if on_path else None


def pick_model_directory(sparse: Path) -> Path | None:
    """Which of the mapper's models to carry forward, if it wrote any.

    COLMAP writes ``sparse/0``, ``sparse/1`` and so on - one per group of
    photographs it could connect - and writes *nothing* when it could connect
    none, while still exiting zero. So "no directory" is the failure that
    matters, and several directories means the capture came apart into pieces,
    of which the largest is the one worth having.
    """
    if not sparse.is_dir():
        return None
    models = [d for d in sparse.iterdir() if d.is_dir() and (d / "images.bin").is_file()]
    if not models:
        return None
    return max(models, key=lambda d: (d / "images.bin").stat().st_size)


def newest_log(directory: Path) -> str:
    """The tail of the most recent OpenMVS log, which is where its errors go.

    Returns a sentence rather than nothing when there is no log: a failure with
    no explanation at all is worse than one that admits it has none.
    """
    try:
        logs = sorted(directory.glob("*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return "no log could be read"
    if not logs:
        return "it wrote no log"
    try:
        lines = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "its log could not be read"
    # The memory dump it ends every run with says nothing about what went wrong.
    useful = [line for line in lines if "INFO" not in line and "Usage" not in line]
    return "\n".join(useful[-6:]) or "its log said nothing useful"


@dataclass
class ColmapOpenMvsReconstructor:
    """Satisfies ``PhotoReconstructor`` by driving COLMAP and OpenMVS."""

    mesh_io: MeshIO
    colmap: Path | None = field(default_factory=find_colmap)
    openmvs: Path | None = field(default_factory=find_openmvs)
    lease: GpuLease | None = None
    """The card, shared with the mesh generator.

    Injected rather than made here, and deliberately: densification and the
    generator both want most of a 16 GB card, so they must share *one* lease -
    and this package may not reach into the one that owns the concrete
    implementation. The composition root hands the same object to both.
    """

    workspace: Path | None = None
    """Where scratch directories are made. A temporary directory by default."""

    # ----------------------------------------------------------- availability

    @property
    def missing_tools(self) -> tuple[str, ...]:
        """Which OpenMVS tools are absent, so a message can name them."""
        if self.openmvs is None:
            return _OPENMVS_TOOLS
        return tuple(
            name for name in _OPENMVS_TOOLS if not (self.openmvs / _executable(name)).is_file()
        )

    def is_available(self) -> bool:
        """Whether a reconstruction could start right now."""
        lease = self.lease
        return (
            self.colmap is not None and not self.missing_tools and (lease is None or lease.is_free)
        )

    def describe(self) -> str:
        """What is installed and what is not, specifically enough to act on."""
        if self.colmap is None and self.openmvs is None:
            return (
                "Building a model from several photographs needs COLMAP and "
                "OpenMVS. Neither is installed - see docs/12-photogrammetry.md."
            )
        if self.colmap is None:
            return "OpenMVS is here but COLMAP is not - see docs/12-photogrammetry.md."

        missing = self.missing_tools
        if missing:
            return (
                f"COLMAP is here, but OpenMVS is missing {', '.join(missing)}. "
                "The download extracts into a subfolder; its tools need to be "
                "directly inside the folder ModelPop looks in."
            )

        lease = self.lease
        if lease is not None and not lease.is_free:
            return lease.describe()
        return "COLMAP and OpenMVS, ready."

    # -------------------------------------------------------- reconstructing

    def reconstruct(
        self,
        photos: PhotoSet,
        options: ReconstructionOptions | None = None,
        on_progress: Progress | None = None,
    ) -> Result[Reconstruction]:
        """Measure a shape from photographs."""
        settings = options or ReconstructionOptions()

        problem = photos.problem
        if problem is not None:
            return failure("Those photographs cannot be reconstructed", problem)
        if not self.is_available():
            return failure("Reconstruction is not available", self.describe())

        lease = self.lease
        started = time.perf_counter()
        scratch = Path(tempfile.mkdtemp(prefix="modelpop-recon-", dir=self.workspace))
        try:
            if lease is None:
                return self._run(photos, settings, scratch, started, on_progress)
            try:
                with lease.held():
                    return self._run(photos, settings, scratch, started, on_progress)
            except SharedGpuBusyError as busy:
                return failure("The graphics card is busy", str(busy))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _run(
        self,
        photos: PhotoSet,
        settings: ReconstructionOptions,
        scratch: Path,
        started: float,
        on_progress: Progress | None,
    ) -> Result[Reconstruction]:
        """The seven stages, in order, in a scratch directory."""
        images = scratch / "images"
        images.mkdir(parents=True)
        for photo in photos.photos:
            shutil.copy2(photo, images / photo.name)

        database = scratch / "db.db"
        sparse = scratch / "sparse"
        dense = scratch / "dense"
        sparse.mkdir()

        steps = (
            (
                Stage.FEATURES,
                self._colmap_command(
                    "feature_extractor",
                    "--database_path",
                    str(database),
                    "--image_path",
                    str(images),
                    "--FeatureExtraction.use_gpu",
                    "1",
                ),
            ),
            (
                Stage.MATCHING,
                self._colmap_command(
                    "exhaustive_matcher",
                    "--database_path",
                    str(database),
                    "--FeatureMatching.use_gpu",
                    "1",
                ),
            ),
        )
        for stage, command in steps:
            outcome = self._step(stage, command, scratch, settings, on_progress)
            if not outcome.ok:
                return outcome  # type: ignore[return-value]

        # Mapping is judged by its *output*, not by its exit code, because it
        # has two ways of failing and only one of them is loud. Photographs
        # with nothing to match exit non-zero saying "Failed to create any
        # sparse model"; photographs that match but will not connect into one
        # scene exit **zero** having written no model at all. Both mean the
        # same thing to the user, and neither is worth quoting COLMAP for.
        mapped = self._step(
            Stage.MAPPING,
            self._colmap_command(
                "mapper",
                "--database_path",
                str(database),
                "--image_path",
                str(images),
                "--output_path",
                str(sparse),
            ),
            scratch,
            settings,
            on_progress,
        )

        model = pick_model_directory(sparse)
        # A timeout escapes the output-is-the-truth rule, and must. It leaves no
        # model behind either, so without this the user of a large capture is
        # told to go and take *more* photographs - which is the opposite of
        # what would help.
        timed_out = not mapped.ok and _TOO_LONG in mapped.error
        if not mapped.ok and (model is not None or timed_out):
            return mapped  # type: ignore[return-value]
        if model is None:
            return failure(
                "The photographs could not be pieced together",
                "No arrangement of cameras fitted them. That is almost always too "
                "little overlap between shots, or a subject too plain or too shiny "
                "to find detail on. Take more photographs, moving a little between "
                "each, and keep the subject filling the frame.",
            )

        rest = (
            (
                Stage.UNDISTORTING,
                self._colmap_command(
                    "image_undistorter",
                    "--image_path",
                    str(images),
                    "--input_path",
                    str(model),
                    "--output_path",
                    str(dense),
                    "--output_type",
                    "COLMAP",
                ),
            ),
            (
                Stage.HANDOVER,
                self._openmvs_command("InterfaceCOLMAP", "-i", str(dense), "-o", "scene.mvs"),
            ),
            (
                Stage.DENSIFYING,
                self._openmvs_command(
                    "DensifyPointCloud",
                    "scene.mvs",
                    "--resolution-level",
                    str(_RESOLUTION_LEVEL[settings.quality]),
                ),
            ),
            (Stage.MESHING, self._openmvs_command("ReconstructMesh", "scene_dense.mvs")),
        )
        for stage, command in rest:
            outcome = self._step(stage, command, scratch, settings, on_progress)
            if not outcome.ok:
                return outcome  # type: ignore[return-value]

        produced = scratch / "scene_dense_mesh.ply"
        if not produced.is_file():
            return failure(
                "The surface could not be built",
                f"The mesher finished without writing anything. {newest_log(scratch)}",
            )

        return self._finish(produced, photos, model, settings, started)

    # ------------------------------------------------------------- one stage

    def _colmap_command(self, verb: str, *arguments: str) -> list[str]:
        """One COLMAP invocation, as a list.

        A list rather than a joined string - the rule the slicer adapter learned
        the hard way, because a space in a path otherwise becomes two arguments.
        """
        assert self.colmap is not None
        return [str(self.colmap), verb, *arguments]

    def _openmvs_command(self, tool: str, *arguments: str) -> list[str]:
        """One OpenMVS invocation, as a list."""
        assert self.openmvs is not None
        return [str(self.openmvs / _executable(tool)), *arguments, "-w", "."]

    def _step(
        self,
        stage: Stage,
        command: list[str],
        scratch: Path,
        settings: ReconstructionOptions,
        on_progress: Progress | None,
    ) -> Result[str]:
        """Run one stage to completion, or explain why it did not.

        The working directory is the scratch folder for every stage, and that is
        load-bearing rather than tidy: OpenMVS writes both its output files and
        its only diagnostics relative to wherever it was started.
        """
        if on_progress is not None:
            on_progress(progress_through(stage), stage.describe)

        remaining = settings.timeout_seconds - 0.0
        try:
            finished = subprocess.run(
                command,
                cwd=scratch,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(remaining, 60.0),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return failure(
                f"{stage.describe} {_TOO_LONG}",
                f"It ran for over {settings.timeout_seconds / 60:.0f} minutes. Fewer "
                "or smaller photographs, or a lower quality setting, will finish.",
            )
        except OSError as error:
            return failure(f"{stage.describe} could not be started", str(error))

        if finished.returncode != 0:
            # COLMAP says why on its own streams; OpenMVS says nothing there and
            # writes to a log instead, so both are offered rather than guessed at.
            spoken = (finished.stderr or finished.stdout or "").strip()
            detail = spoken[-500:] if spoken else newest_log(scratch)
            return failure(f"{stage.describe} failed", detail)

        return success(stage.name)

    # --------------------------------------------------------------- finish

    def _finish(
        self,
        produced: Path,
        photos: PhotoSet,
        model: Path,
        settings: ReconstructionOptions,
        started: float,
    ) -> Result[Reconstruction]:
        """Read the mesh back, tidy it, and say how the run went."""
        loaded = self.mesh_io.load(produced)
        if not loaded.ok:
            return failure(
                "The reconstructed surface could not be read",
                "The mesher wrote a file ModelPop could not open.",
            )

        mesh = loaded.unwrap()
        discarded = 0.0
        if settings.keep_largest_piece_only:
            mesh, discarded = _largest_piece(mesh)

        notes: list[str] = []
        mesh, scale_note = _given_a_size(mesh, settings)
        notes.extend(scale_note)
        if mesh.triangle_count > _DISPLAY_BUDGET:
            notes.append(
                f"{mesh.triangle_count:,} triangles, which is more than the viewport "
                "draws comfortably. Simplify it before printing if it feels slow."
            )
        notes.append(
            "Photogrammetry only sees what the camera saw, so this has holes where "
            "it did not look. Repair closes them."
        )

        return success(
            Reconstruction(
                mesh=mesh,
                photos_given=len(photos),
                photos_used=_registered_in(model),
                seconds=time.perf_counter() - started,
                discarded_fraction=discarded,
                notes=tuple(notes),
            )
        )


def _registered_in(model: Path) -> int:
    """How many photographs the solver actually placed.

    Read from the model's own listing rather than assumed to be all of them,
    because the number that matters is how many it *could* place. COLMAP writes
    a text `images.txt` only on request, so this counts the binary records by
    their header, and falls back to zero rather than guessing.
    """
    listing = model / "images.bin"
    try:
        with listing.open("rb") as handle:
            return int.from_bytes(handle.read(8), "little", signed=False)
    except (OSError, ValueError):
        return 0


def _largest_piece(mesh: Mesh) -> tuple[Mesh, float]:
    """Keep the biggest connected lump and say how much went.

    A capture of an object on a table is also a capture of the table, and of
    whatever else was in shot. Returning the fraction discarded matters: losing
    half the surface is either exactly right or completely wrong, and only the
    user knows which.
    """
    import trimesh

    try:
        body = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
        pieces = body.split(only_watertight=False)
    except Exception:
        return mesh, 0.0

    if len(pieces) <= 1:
        return mesh, 0.0

    biggest = max(pieces, key=lambda p: len(p.faces))
    kept = len(biggest.faces)
    total = sum(len(p.faces) for p in pieces)

    import numpy as np

    from modelpop.domain.mesh import Mesh as DomainMesh

    return (
        DomainMesh(
            np.asarray(biggest.vertices, dtype=np.float64),
            np.asarray(biggest.faces, dtype=np.int32),
        ),
        1.0 - kept / total if total else 0.0,
    )


def _given_a_size(mesh: Mesh, settings: ReconstructionOptions) -> tuple[Mesh, tuple[str, ...]]:
    """Scale the model, and say where the size came from.

    A reconstruction is a shape without units: the solver fixes the geometry up
    to an arbitrary factor and nothing in the photographs says how big any of it
    was. The note is the point. A size measured against a reference in shot can
    be checked with calipers; a size the app chose cannot, and on screen the two
    are indistinguishable.
    """
    largest = mesh.bounds.largest_dimension.millimetres
    if largest <= 0:
        return mesh, ("The reconstruction has no size at all.",)

    note = (
        f"Scaled to {settings.size.format()} at its largest, measured against a "
        "reference in the photographs."
        if settings.size_was_measured
        else f"Photographs carry no scale, so this was made {settings.size.format()} "
        "at its largest. Use Resize for the real size, or include a ruler in the "
        "shot next time and measure against it."
    )
    return mesh.scaled_to_fit(settings.size), (note,)
