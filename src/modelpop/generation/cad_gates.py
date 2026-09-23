"""The gates a generated part must pass.

Pipeline A from ``docs/03-pipelines.md``. A language model writes build123d
code, the code runs, and then the result is *checked* - because the 2026
benchmarks are unanimous that models get the shape roughly right and the
numbers wrong.

Two principles run through this module:

**Feed back measurements, not pictures.** "The hole spacing is 31.4 mm, the spec
says 32.0 plus or minus 0.2" is something a model can act on. A render is not.
Published results show image feedback barely helps editing; numbers do.

**Fail at the first gate.** There is no point checking dimensions on a script
that did not run, and telling a model six things at once produces worse
corrections than telling it one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from modelpop.application.cad_ports import DimensionTable, ScriptResult
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.readiness import MeshFacts

__all__ = ["Gate", "GateReport", "GateResult", "evaluate"]


class Gate(Enum):
    """The checks, in the order they are applied."""

    EXECUTES = "executes"
    HAS_VOLUME = "has-volume"
    SINGLE_SOLID = "single-solid"
    WATERTIGHT = "watertight"
    DIMENSIONS = "dimensions"
    FITS_PRINTER = "fits-printer"

    @property
    def description(self) -> str:
        """What this gate is checking, for the UI and for logs."""
        return {
            Gate.EXECUTES: "the script runs",
            Gate.HAS_VOLUME: "it produces a solid",
            Gate.SINGLE_SOLID: "it produces one connected part",
            Gate.WATERTIGHT: "the part is watertight",
            Gate.DIMENSIONS: "the part measures what was asked for",
            Gate.FITS_PRINTER: "the part fits the printer",
        }[self]


@dataclass(frozen=True, slots=True)
class GateResult:
    """Whether one gate passed, and what to say if it did not."""

    gate: Gate
    passed: bool
    feedback: str = ""
    """What to tell the model. Numeric and specific, never a vague complaint."""

    def __str__(self) -> str:
        mark = "pass" if self.passed else "FAIL"
        return f"[{mark}] {self.gate.description}" + (
            f" - {self.feedback}" if self.feedback else ""
        )


@dataclass(frozen=True, slots=True)
class GateReport:
    """How a generated part fared."""

    results: tuple[GateResult, ...] = ()

    @property
    def passed(self) -> bool:
        """Whether every gate that ran was cleared."""
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def first_failure(self) -> GateResult | None:
        """The gate that stopped it, if any."""
        return next((r for r in self.results if not r.passed), None)

    @property
    def feedback(self) -> str:
        """The single correction to send back, or empty when all is well.

        Deliberately one thing. A model given six simultaneous complaints
        produces a worse next attempt than one given the most important.
        """
        failure = self.first_failure
        return failure.feedback if failure else ""

    @property
    def score(self) -> float:
        """Fraction of gates cleared, for ranking several attempts.

        Best-of-N needs an ordering even when nothing passed outright.
        """
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def __str__(self) -> str:
        return "\n".join(str(r) for r in self.results)


def evaluate(
    result: ScriptResult,
    table: DimensionTable | None = None,
    printer: PrinterProfile | None = None,
    facts: MeshFacts | None = None,
    *,
    expect_single_solid: bool = True,
) -> GateReport:
    """Run the gates against a script result, stopping at the first failure.

    Args:
        result: what the script produced.
        table: the dimensions the user asked for, if any were extracted.
        printer: the printer to check against; a P2S by default.
        facts: mesh measurements, when they have already been taken. Without
            them the watertight gate is skipped rather than guessed at.
        expect_single_solid: whether several disconnected solids is a failure.
            True for most parts; false when the request was for an assembly.
    """
    target = printer or PrinterProfile.p2s()
    checks: list[GateResult] = []

    checks.append(GateResult(Gate.EXECUTES, True))

    volume = result.measurements.volume_mm3
    if volume <= 0:
        checks.append(
            GateResult(
                Gate.HAS_VOLUME,
                False,
                "the script ran but the result has no volume - the operations may "
                "have cancelled each other out, or a boolean removed everything",
            )
        )
        return GateReport(tuple(checks))
    checks.append(GateResult(Gate.HAS_VOLUME, True))

    if expect_single_solid and result.measurements.solid_count > 1:
        checks.append(
            GateResult(
                Gate.SINGLE_SOLID,
                False,
                f"the result is {result.measurements.solid_count} separate solids; it "
                "should be one connected part. Check that every feature touches the body.",
            )
        )
        return GateReport(tuple(checks))
    checks.append(GateResult(Gate.SINGLE_SOLID, True))

    if facts is not None:
        if not facts.is_watertight:
            checks.append(
                GateResult(
                    Gate.WATERTIGHT,
                    False,
                    "the exported part is not watertight, so it cannot be printed. "
                    "This usually means a surface was built instead of a solid.",
                )
            )
            return GateReport(tuple(checks))
        checks.append(GateResult(Gate.WATERTIGHT, True))

    if table:
        dimension_check = _check_dimensions(result, table)
        checks.append(dimension_check)
        if not dimension_check.passed:
            return GateReport(tuple(checks))

    fit = _check_fits(result, target)
    checks.append(fit)
    return GateReport(tuple(checks))


def _check_dimensions(result: ScriptResult, table: DimensionTable) -> GateResult:
    """Compare the solid against the dimensions that were asked for.

    Only bounding-box dimensions are checked here. Anything named ``width``,
    ``depth``, ``height``, ``length`` or ``diameter`` is matched against the
    measured extents; other named dimensions need a feature-level measurement
    that the worker does not yet take, so they are skipped rather than guessed.
    """
    measured = {
        "width": result.measurements.width,
        "length": result.measurements.width,
        "depth": result.measurements.depth,
        "height": result.measurements.height,
        "thickness": result.measurements.height,
    }

    problems = [
        dimension.describe_failure(measured[dimension.name.lower()])
        for dimension in table.dimensions
        if dimension.name.lower() in measured
        and not dimension.accepts(measured[dimension.name.lower()])
    ]
    if problems:
        return GateResult(Gate.DIMENSIONS, False, "; ".join(problems))
    return GateResult(Gate.DIMENSIONS, True)


def _check_fits(result: ScriptResult, printer: PrinterProfile) -> GateResult:
    """Check the part fits the build volume."""
    width, depth, height = printer.envelope
    measurements = result.measurements
    too_big = (
        measurements.width.millimetres > width.millimetres
        or measurements.depth.millimetres > depth.millimetres
        or measurements.height.millimetres > height.millimetres
    )
    if too_big:
        return GateResult(
            Gate.FITS_PRINTER,
            False,
            f"the part is {measurements.width.format()} x {measurements.depth.format()} x "
            f"{measurements.height.format()}, larger than the "
            f"{width.format(places=0)} build volume. Make it smaller, or split it.",
        )
    return GateResult(Gate.FITS_PRINTER, True)
