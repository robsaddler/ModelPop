"""The generation port, and the vocabulary it speaks.

``Workspace`` needs to turn a description into a part. It must not need to know
*how*: that involves a language model, a sandboxed subprocess and a correction
loop, none of which belong in the application layer.

So the types that describe **what happened** live here, and the code that makes
it happen lives in ``modelpop.generation`` behind :class:`PartGenerator`. The
split is what lets the whole generation path be replaced by a scripted fake in
tests that run in milliseconds and spend nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from modelpop.application.ai_ports import AiSettings
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.result import Result

if TYPE_CHECKING:
    from modelpop.application.cad_ports import DimensionTable, ScriptResult

__all__ = [
    "Attempt",
    "CadGenerationRun",
    "Gate",
    "GateReport",
    "GateResult",
    "PartGenerator",
]


class Gate(Enum):
    """The checks a generated part must clear, in the order they are applied."""

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


@dataclass(frozen=True, slots=True)
class Attempt:
    """One pass round the generate-and-correct loop."""

    number: int
    script: str
    report: GateReport | None = None
    result: ScriptResult | None = None
    error: str = ""
    cost_usd: float = 0.0

    @property
    def succeeded(self) -> bool:
        """Whether this attempt cleared every gate."""
        return self.report is not None and self.report.passed

    @property
    def score(self) -> float:
        """How close this attempt came, for picking the best of several."""
        return self.report.score if self.report is not None else 0.0

    def summarise(self) -> str:
        """A line for the run log."""
        if self.succeeded:
            return f"Attempt {self.number}: passed"
        problem = self.error or (self.report.feedback if self.report else "unknown")
        return f"Attempt {self.number}: {problem[:120]}"


@dataclass(frozen=True, slots=True)
class CadGenerationRun:
    """Everything that happened during one generation."""

    attempts: tuple[Attempt, ...] = ()
    best: Attempt | None = None
    total_cost_usd: float = 0.0
    stopped_because: str = ""

    @property
    def succeeded(self) -> bool:
        """Whether a fully passing part was produced."""
        return self.best is not None and self.best.succeeded

    def log(self) -> str:
        """The run, as a few readable lines."""
        lines = [attempt.summarise() for attempt in self.attempts]
        if self.stopped_because:
            lines.append(self.stopped_because)
        lines.append(f"Spent about ${self.total_cost_usd:.3f}")
        return "\n".join(lines)


@runtime_checkable
class PartGenerator(Protocol):
    """Turning a description into a part, and changing one already made.

    Both methods return a whole run rather than just the geometry, because the
    user is paying for every attempt and is owed the log.
    """

    def is_ready(self) -> bool:
        """Whether generation can run at all right now.

        False when the API key is missing or the CAD kernel failed to load, so
        the UI can disable the button instead of failing at the click.
        """
        ...

    def generate(
        self,
        request: str,
        *,
        printer: PrinterProfile,
        table: DimensionTable | None = None,
        settings: AiSettings | None = None,
    ) -> Result[CadGenerationRun]:
        """Produce a part from a description."""
        ...

    def edit(
        self,
        script: str,
        instruction: str,
        *,
        printer: PrinterProfile,
        table: DimensionTable | None = None,
        settings: AiSettings | None = None,
    ) -> Result[CadGenerationRun]:
        """Change a part by describing the change."""
        ...
