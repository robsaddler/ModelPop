"""Changing a model by describing the change.

The prompt goes to a language model, the model asks for typed commands, and
those commands go on the bus exactly as a toolbar click would. So an AI edit
joins the feature tree, is labelled with who asked for it, and is undoable -
none of which needed a special case, because it is not one.

**One command failing does not lose the rest.** A model asked to "round the
corners and hollow it out" may get the fillet right and the wall thickness
wrong. Applying them one at a time and reporting which stuck is more useful than
refusing the lot, and every applied command is individually undoable anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modelpop.application.ai_ports import AiSettings, Conversation, Message, ModelRole
from modelpop.domain.commands import Origin
from modelpop.domain.result import Result, failure, success
from modelpop.generation.command_prompt import (
    SYSTEM_PROMPT,
    build_edit_request,
    build_new_request,
    read_commands,
)

if TYPE_CHECKING:
    from modelpop.application.ai_ports import ChatProvider
    from modelpop.application.modelling import ModellingSession, ModelState
    from modelpop.domain.commands import Command

__all__ = ["CommandEditRun", "edit_by_description"]


@dataclass(frozen=True, slots=True)
class CommandEditRun:
    """What one described change actually did."""

    applied: tuple[str, ...] = ()
    """The changes that stuck, in words."""

    refused: tuple[tuple[str, str], ...] = ()
    """Changes the kernel would not make, with why."""

    discarded: tuple[str, ...] = ()
    """Operations the model asked for that do not exist."""

    cost_usd: float = 0.0
    state: ModelState | None = None

    @property
    def changed_anything(self) -> bool:
        """Whether the model is different from before."""
        return bool(self.applied)

    def summary(self) -> str:
        """A line for the status bar, honest about what did not happen."""
        if not self.applied and not self.refused:
            return "Nothing was changed."

        parts = []
        if self.applied:
            parts.append(f"Applied {len(self.applied)}: {'; '.join(self.applied)}")
        if self.refused:
            parts.append(f"{len(self.refused)} could not be made")
        if self.discarded:
            parts.append(f"{len(self.discarded)} asked for something that does not exist")
        return ". ".join(parts) + "."

    def detail(self) -> str:
        """The full account, for the run log."""
        lines = [f"Applied: {line}" for line in self.applied]
        lines += [f"Refused: {name} - {why}" for name, why in self.refused]
        lines += [f"Not a real operation: {name}" for name in self.discarded]
        lines.append(f"Spent about ${self.cost_usd:.3f}")
        return "\n".join(lines)


def edit_by_description(
    session: ModellingSession,
    instruction: str,
    provider: ChatProvider,
    settings: AiSettings | None = None,
) -> Result[CommandEditRun]:
    """Ask a model to build or change the open part, and apply what it asks for.

    Builds when the session is empty and changes when it is not, which is the
    same journey from the user's side: they say what they want in words, and
    the result is an ordinary feature tree they can then edit with the toolbar.

    Args:
        session: the parametric model to build or change.
        instruction: what the user said, in their own words.
        provider: the model to ask.
        settings: which model, and what it may spend.
    """
    if not instruction.strip():
        return failure("Nothing was asked for")
    if not provider.is_configured():
        return failure("No AI provider configured", "Add an API key in Settings.")

    limits = settings or AiSettings()
    conversation = Conversation(
        system=SYSTEM_PROMPT,
        messages=(Message.user(_describe(session, instruction)),),
    )

    answered = provider.complete(conversation, limits.choice_for(ModelRole.CAD_CODEGEN))
    if not answered.ok:
        return answered  # type: ignore[return-value]

    completion = answered.unwrap()
    if completion.refused:
        return failure(
            "The model declined to make that change",
            completion.refusal_category or "It did not say why.",
        )

    parsed = read_commands(completion.text)
    if not parsed.ok:
        return parsed  # type: ignore[return-value]

    commands, discarded = parsed.unwrap()
    return success(_apply_each(session, commands, discarded, completion.estimated_cost_usd))


def _describe(session: ModellingSession, instruction: str) -> str:
    """The user message: the current model, and what is wanted of it.

    An empty session is a *new part*, not a change to nothing, and is asked for
    in those terms. The reply comes back as the same typed commands either way,
    which is the whole point: a part built from a description lands in the
    feature tree and the toolbar can then work on it, rather than being a
    script only a language model can edit.
    """
    state = session.state
    if state.is_empty:
        return build_new_request(instruction)

    tree = "\n".join(f"{line.index + 1}. {line.label}" for line in state.features)
    size = ""
    if state.measurements is not None:
        measured = state.measurements
        size = (
            f"{measured.width.format(places=1)} x {measured.depth.format(places=1)} x "
            f"{measured.height.format(places=1)}"
        )
    return build_edit_request(instruction, tree, size)


def _apply_each(
    session: ModellingSession,
    commands: list[Command],
    discarded: tuple[str, ...],
    cost: float,
) -> CommandEditRun:
    """Apply commands one at a time, keeping whatever works.

    Each goes through the session, so each is rolled back on its own if the
    kernel refuses it, and the model is left in a state that builds however many
    of them stuck.
    """
    applied: list[str] = []
    refused: list[tuple[str, str]] = []

    for command in commands:
        outcome = session.apply(command, Origin.ASSISTANT)
        if outcome.ok:
            applied.append(command.describe())
        else:
            refused.append((command.describe(), outcome.error))

    return CommandEditRun(
        applied=tuple(applied),
        refused=tuple(refused),
        discarded=discarded,
        cost_usd=cost,
        state=session.state,
    )
