"""The generate-and-correct loop for mechanical parts.

A model writes build123d code, the code runs, the gates measure the result, and
the failure - one failure, phrased numerically - goes back for another attempt.
Published results are consistent that this loop matters more than model choice:
the same model with a measure-and-correct harness clears a far higher share of
CAD benchmarks than without one.

Three deliberate choices:

**Keep the whole conversation.** Each attempt sees what it wrote last time and
what was wrong with it. Starting fresh each round throws away the only thing
that makes the next attempt better.

**Keep the best attempt, not the last.** Attempt three can be worse than attempt
two. Scoring every attempt means a run that never fully passes still returns the
closest thing it managed.

**Stop spending when told to.** A loop that calls a paid API needs a ceiling the
user sets, not one the author guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from modelpop.application.ai_ports import (
    AiSettings,
    ChatProvider,
    Conversation,
    Message,
    ModelRole,
)
from modelpop.application.cad_ports import DimensionTable
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.result import Result, failure, success
from modelpop.generation.cad_gates import GateReport, evaluate
from modelpop.generation.cad_prompt import SYSTEM_PROMPT, build_request

if TYPE_CHECKING:
    from modelpop.application.cad_ports import CadKernel, ScriptResult
    from modelpop.application.ports import MeshOps

__all__ = ["Attempt", "CadGenerationRun", "generate_part"]

_CODE_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Attempt:
    """One pass round the loop."""

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


def extract_script(reply: str) -> str:
    """Pull the code out of a model's reply.

    Models wrap code in fences most of the time and occasionally do not. Taking
    the longest fenced block handles the common case of a short illustrative
    snippet followed by the real answer; falling back to the whole reply handles
    a model that simply wrote code.
    """
    blocks: list[str] = _CODE_FENCE.findall(reply)
    if blocks:
        return max(blocks, key=len).strip()
    return reply.strip()


@dataclass
class _Budget:
    """Tracks what a run has spent and whether it may continue."""

    limit_usd: float
    spent_usd: float = 0.0

    def record(self, cost: float) -> None:
        """Add the cost of one call."""
        self.spent_usd += cost

    @property
    def exhausted(self) -> bool:
        """Whether the run has spent its allowance. Zero means no limit."""
        return self.limit_usd > 0 and self.spent_usd >= self.limit_usd


def generate_part(
    request: str,
    provider: ChatProvider,
    kernel: CadKernel,
    mesh_ops: MeshOps | None = None,
    table: DimensionTable | None = None,
    printer: PrinterProfile | None = None,
    settings: AiSettings | None = None,
) -> Result[CadGenerationRun]:
    """Generate a mechanical part, correcting it until it measures up.

    Args:
        request: what the user asked for, in their own words.
        provider: the model that writes the code.
        kernel: runs the code and measures the solid.
        mesh_ops: used for the watertight gate. Without it that gate is skipped
            rather than guessed at.
        table: the dimensions the part must hit, if any were extracted.
        printer: the printer to check against.
        settings: attempt and spend limits.

    Returns a run describing every attempt, whether or not one succeeded. A run
    that never passes still returns its closest attempt, because a nearly right
    part the user can edit beats nothing at all.
    """
    if not request.strip():
        return failure("Nothing to make", "describe the part you want")
    if not provider.is_configured():
        return failure(
            "No AI provider configured",
            "Add an API key in Settings before generating a part.",
        )
    if not kernel.is_available():
        return failure(
            "The CAD kernel is unavailable",
            "build123d could not be loaded. Reinstall the application's dependencies.",
        )

    config = settings or AiSettings()
    choice = config.choice_for(ModelRole.CAD_CODEGEN)
    budget = _Budget(config.spend_limit_usd)

    conversation = Conversation(
        system=SYSTEM_PROMPT,
        messages=(Message.user(build_request(request, table, printer)),),
    )

    attempts: list[Attempt] = []
    stopped = ""

    for number in range(1, max(1, config.max_attempts) + 1):
        completion = provider.complete(conversation, choice)
        if not completion.ok:
            stopped = f"Stopped: {completion.error}"
            break

        reply = completion.unwrap()
        budget.record(reply.estimated_cost_usd)

        if reply.refused:
            stopped = "The model declined this request" + (
                f" ({reply.refusal_category})" if reply.refusal_category else ""
            )
            break

        attempt = _try_script(number, extract_script(reply.text), kernel, mesh_ops, table, printer)
        attempts.append(replace(attempt, cost_usd=reply.estimated_cost_usd))

        if attempt.succeeded:
            break

        if budget.exhausted:
            stopped = f"Stopped: reached the ${config.spend_limit_usd:.2f} spend limit"
            break

        conversation = conversation.then(Message.assistant(reply.text)).then(
            Message.user(_correction(attempt))
        )

    if not attempts:
        return failure("Could not generate the part", stopped or "no attempt completed")

    best = max(attempts, key=lambda a: (a.succeeded, a.score))
    return success(
        CadGenerationRun(
            attempts=tuple(attempts),
            best=best,
            total_cost_usd=budget.spent_usd,
            stopped_because=stopped,
        )
    )


def _try_script(
    number: int,
    script: str,
    kernel: CadKernel,
    mesh_ops: MeshOps | None,
    table: DimensionTable | None,
    printer: PrinterProfile | None,
) -> Attempt:
    """Run one script and put it through the gates."""
    if not script:
        return Attempt(number, script, error="the model replied without any code")

    outcome = kernel.run(script)
    if not outcome.ok:
        return Attempt(number, script, error=outcome.error)

    produced = outcome.unwrap()
    facts = mesh_ops.inspect(produced.mesh) if mesh_ops is not None else None
    report = evaluate(produced, table, printer, facts)
    return Attempt(number, script, report=report, result=produced)


def _correction(attempt: Attempt) -> str:
    """What to send back after a failed attempt.

    One problem, stated numerically, with an explicit instruction to return the
    whole script. Asking for a patch invites a reply that cannot be run.
    """
    problem = attempt.error or (attempt.report.feedback if attempt.report else "it did not work")
    return (
        f"That attempt did not work: {problem}\n\n"
        "Fix that one problem and return the complete corrected script. "
        "Return only the code, in a single Python block."
    )
