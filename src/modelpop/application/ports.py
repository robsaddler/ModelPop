"""The ports: what the application needs, expressed as protocols.

A port is defined by what the *application* needs, never by what a library
offers. If ``CadKernel`` starts leaking OCCT types, the abstraction has failed
and the kernel is no longer swappable.

These are ``Protocol`` classes rather than base classes, so an adapter satisfies
a port by shape alone and never imports the application to do it. That keeps the
dependency arrow pointing inwards, which ``import-linter`` enforces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import PrinterProfile, SupportStyle, SupportType
from modelpop.domain.readiness import Finding, MeshFacts
from modelpop.domain.result import Result
from modelpop.domain.units import Length

__all__ = [
    "GcodeVerifier",
    "MeshIO",
    "MeshOps",
    "SliceJob",
    "SliceReport",
    "Slicer",
]


@runtime_checkable
class MeshIO(Protocol):
    """Reading and writing mesh files."""

    def load(self, path: Path) -> Result[Mesh]:
        """Read a mesh from disk.

        Implementations must not raise for a malformed file; a bad file is an
        expected outcome and belongs in the ``Result``.
        """
        ...

    def save(self, mesh: Mesh, path: Path) -> Result[Path]:
        """Write a mesh to disk, choosing the format from the suffix."""
        ...

    def supported_suffixes(self) -> frozenset[str]:
        """Lower-case suffixes this implementation can read, including the dot."""
        ...


@runtime_checkable
class MeshOps(Protocol):
    """Geometry operations on triangle meshes.

    Every implementation must satisfy the same contract suite
    (``tests/geometry/test_mesh_ops_contract.py``). That is the Liskov check:
    swapping one adapter for another must not silently change behaviour.
    """

    def inspect(self, mesh: Mesh) -> MeshFacts:
        """Measure everything the readiness rules need, in one pass."""
        ...

    def repair(self, mesh: Mesh) -> Result[Mesh]:
        """Make the mesh watertight and consistently wound.

        Must return a failure rather than a broken mesh. Reporting success while
        leaving the mesh unusable is the single worst thing an implementation
        can do, because everything downstream trusts this.
        """
        ...

    def normalise(self, mesh: Mesh) -> Mesh:
        """Merge duplicate vertices, drop degenerate faces and tiny islands."""
        ...

    def thicken(self, mesh: Mesh, by: Length) -> Result[Mesh]:
        """Grow every surface outwards, so a thin wall becomes a thicker one.

        A wall gains twice the distance given, because both of its faces move.
        Must refuse rather than return a mesh that stopped being a solid.
        """
        ...

    def decimate(self, mesh: Mesh, target_triangles: int) -> Result[Mesh]:
        """Reduce the triangle count while preserving the silhouette."""
        ...

    def union(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        """Boolean union."""
        ...

    def difference(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        """Boolean subtraction of ``right`` from ``left``."""
        ...

    def intersection(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        """Boolean intersection."""
        ...


@dataclass(frozen=True, slots=True)
class SliceJob:
    """Everything the slicer needs for one run."""

    model_path: Path
    printer: PrinterProfile
    output_dir: Path
    supports: SupportType = SupportType.NONE
    support_style: SupportStyle = SupportStyle.DEFAULT

    auto_orient: bool = False
    """Whether the slicer may turn the model to suit itself.

    Off, because the user turned it on purpose. Standing a model up is a step
    in the feature tree, it undoes, and it is often the whole point - letting
    the slicer overrule it silently would make the viewport a lie."""

    auto_arrange: bool = False
    """Whether the slicer may move the model about the plate.

    Off, because ModelPop has already centred it - and because the slicer's
    idea of a good arrangement is not ours. Measured on a 40x30x20 box written
    dead centre at (128, 128): with arranging on it printed at (100, 100),
    twenty-eight millimetres out in both directions. On a model the size of the
    dragon that is the difference between the middle of the plate and hanging
    off a corner, which is exactly how it was reported."""
    plate: int = 0
    """Which plate to slice; ``0`` means every plate."""


@dataclass(frozen=True, slots=True)
class SliceObject:
    """One object as the slicer placed it."""

    name: str
    triangle_count: int
    width: Length
    depth: Length
    height: Length


@dataclass(frozen=True, slots=True)
class SliceReport:
    """What came back from a slice.

    The field names mirror the slicer's own ``result.json`` where sensible, so
    the mapping stays obvious. See ``docs/research/spike-bambu-cli.md``.
    """

    succeeded: bool
    message: str
    gcode_path: Path | None = None
    project_path: Path | None = None
    predicted_seconds: float = 0.0
    layer_height: Length | None = None
    wall_loops: int = 0
    infill_density: float = 0.0
    supports_generated: bool = False
    filament_change_count: int = 0
    grams_used: float = 0.0
    grams_purged: float = 0.0
    """Filament wasted on tool changes - the "poop". Zero on a single-colour print."""
    objects: tuple[SliceObject, ...] = ()
    feature_seconds: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def predicted_duration(self) -> str:
        """Predicted print time as ``"2h 31m"``."""
        total = int(self.predicted_seconds)
        hours, remainder = divmod(total, 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"

    @property
    def bridge_seconds(self) -> float:
        """Time spent bridging.

        A lot of bridge time on a model the user thinks is simple usually means
        a poor orientation, so it is worth surfacing rather than burying.
        """
        return self.feature_seconds.get("Bridge", 0.0)


@runtime_checkable
class Slicer(Protocol):
    """Turning a model into machine instructions.

    ModelPop does not implement slicing or support generation (ADR-0006): that
    is thousands of lines of well-tuned code in existing slicers, with no
    embeddable library. We prepare a clean, oriented, scaled model and hand it over.
    """

    def is_available(self) -> bool:
        """Whether the slicer is installed and usable right now."""
        ...

    def describe(self) -> str:
        """Which slicer and which version, for diagnostics and bug reports."""
        ...

    def slice(self, job: SliceJob) -> Result[SliceReport]:
        """Slice a model.

        A slicer that refuses the model is an expected outcome, not an
        exception: it belongs in the ``Result``.
        """
        ...


@runtime_checkable
class GcodeVerifier(Protocol):
    """Reading a toolpath back to see whether it will actually print.

    A separate port from the slicer because it answers a different question.
    The slicer says "here is the G-code"; this says "here is what will go wrong
    when you run it", and only the toolpath can answer that - the mesh cannot.
    """

    def verify(self, gcode: Path, printer: PrinterProfile) -> tuple[Finding, ...]:
        """Findings about the toolpath, or an empty tuple when all is well.

        Must return empty rather than raising when the file cannot be read: a
        slice that succeeded must not be reported as a failure because an extra
        check could not run.
        """
        ...
