"""Ports for parametric CAD.

Separate from ``ports.py`` because the CAD path has a distinct shape: it deals
in *solids* and *scripts* rather than meshes, and it is the only part of the
system that executes generated code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from modelpop.domain.mesh import Mesh
from modelpop.domain.result import Result
from modelpop.domain.units import Length

__all__ = [
    "CadKernel",
    "Dimension",
    "DimensionTable",
    "ScriptResult",
    "SolidMeasurements",
]


@dataclass(frozen=True, slots=True)
class Dimension:
    """One dimension the user asked for, with the tolerance they will accept.

    Every 2026 benchmark says the same thing: language models get the global
    shape right and the precise numbers wrong. So the numbers are extracted from
    the request up front and then *verified against the solid*, rather than
    trusted because the model said so.
    """

    name: str
    expected: Length
    tolerance: Length = field(default_factory=lambda: Length.mm(0.2))

    def accepts(self, measured: Length) -> bool:
        """Whether a measured value is within tolerance."""
        return abs(measured.millimetres - self.expected.millimetres) <= self.tolerance.millimetres

    def describe_failure(self, measured: Length) -> str:
        """Why a measurement was rejected, phrased so a model can act on it.

        Numeric and specific: feeding back "the hole spacing is 31.4 mm, the
        spec says 32.0 plus or minus 0.2" works far better than showing a
        picture and hoping.
        """
        delta = measured.millimetres - self.expected.millimetres
        direction = "too large" if delta > 0 else "too small"
        return (
            f"{self.name} measured {measured.format()}, but should be "
            f"{self.expected.format()} +/- {self.tolerance.format()} "
            f"({abs(delta):.2f} mm {direction})"
        )


@dataclass(frozen=True, slots=True)
class DimensionTable:
    """Everything the finished part must measure."""

    dimensions: tuple[Dimension, ...] = ()

    def __len__(self) -> int:
        return len(self.dimensions)

    def __bool__(self) -> bool:
        return bool(self.dimensions)


@dataclass(frozen=True, slots=True)
class SolidMeasurements:
    """What a generated solid actually turned out to be.

    Used to check the model honoured the dimension table, and to catch the
    topology failures that a picture would not show.
    """

    volume_mm3: float
    width: Length
    depth: Length
    height: Length
    face_count: int = 0
    edge_count: int = 0
    vertex_count: int = 0
    solid_count: int = 1
    is_valid: bool = True

    @property
    def euler_characteristic(self) -> int:
        """``V - E + F``. For a simple closed solid this is 2.

        A different value means handles or voids, which is sometimes intended
        and often a sign the model produced something it did not mean to.
        """
        return self.vertex_count - self.edge_count + self.face_count


@dataclass(frozen=True, slots=True)
class ScriptResult:
    """The outcome of running a generated CAD script."""

    mesh: Mesh
    measurements: SolidMeasurements
    step_path: Path | None = None
    stdout: str = ""
    duration_seconds: float = 0.0


@runtime_checkable
class CadKernel(Protocol):
    """Executing parametric CAD scripts and measuring the result.

    The only part of ModelPop that runs generated code, so the only part with a
    genuine sandboxing requirement. Implementations must execute in a separate
    process with no network access, a confined working directory, and a
    wall-clock timeout.
    """

    def is_available(self) -> bool:
        """Whether the kernel can run right now."""
        ...

    def describe(self) -> str:
        """Which kernel and which version, for diagnostics."""
        ...

    def run(self, script: str, timeout_seconds: float = 60.0) -> Result[ScriptResult]:
        """Execute a script and return the solid it produced.

        A script that fails to compile, produces nothing, or times out is an
        expected outcome: it is how the generate-and-correct loop learns.
        """
        ...
