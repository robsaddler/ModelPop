"""Ports for talking to a language model.

Deliberately segregated. Different stages need different things: writing CAD
code needs reasoning, judging a render needs vision, and routing a request needs
neither and should be cheap. A single fat interface would force every adapter to
pretend it supports everything, and local models genuinely do not.

An adapter declares what it can do and is honest about the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from modelpop.domain.result import Result

__all__ = [
    "ChatProvider",
    "Completion",
    "Conversation",
    "Message",
    "ModelRole",
    "Role",
    "VisionProvider",
]


class Role(Enum):
    """Who is speaking."""

    USER = "user"
    ASSISTANT = "assistant"


class ModelRole(Enum):
    """What a model is being asked to do here.

    Model choice is made per role rather than globally, because these jobs have
    genuinely different requirements and costs. Writing parametric CAD wants the
    strongest reasoning available; deciding whether a request is a bracket or a
    dragon does not and should not cost the same.
    """

    ROUTING = "routing"
    """Classify the request and extract its dimensions. Cheap and frequent."""

    CAD_CODEGEN = "cad-codegen"
    """Write build123d code. The hardest job, and worth the best model."""

    CRITIQUE = "critique"
    """Judge a render against the request. Needs vision."""

    NAMING = "naming"
    """Name files and describe results. Trivial."""

    @property
    def needs_vision(self) -> bool:
        """Whether this role requires an adapter that can see images."""
        return self is ModelRole.CRITIQUE


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of a conversation.

    Images are raw bytes plus a media type, so the domain never depends on an
    SDK's block format.
    """

    role: Role
    text: str
    images: tuple[tuple[str, bytes], ...] = ()
    """``(media_type, data)`` pairs, such as ``("image/png", b"...")``."""

    @classmethod
    def user(cls, text: str) -> Message:
        """A message from the user."""
        return cls(Role.USER, text)

    @classmethod
    def assistant(cls, text: str) -> Message:
        """A message from the model."""
        return cls(Role.ASSISTANT, text)

    @classmethod
    def with_image(cls, text: str, media_type: str, data: bytes) -> Message:
        """A user message carrying one image."""
        return cls(Role.USER, text, ((media_type, data),))


@dataclass(frozen=True, slots=True)
class Conversation:
    """A system prompt and the turns so far."""

    system: str = ""
    messages: tuple[Message, ...] = ()

    def then(self, message: Message) -> Conversation:
        """A copy with another turn appended."""
        return Conversation(self.system, (*self.messages, message))


@dataclass(frozen=True, slots=True)
class Completion:
    """What the model said, and what it cost."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model: str = ""
    stop_reason: str = ""
    refused: bool = False
    """The model declined. Check this before trusting ``text``."""

    refusal_category: str = ""

    @property
    def estimated_cost_usd(self) -> float:
        """A rough cost, for showing the user what a generation run spent.

        Priced at Claude Opus 5 rates. Approximate by design: the point is to
        let someone see that a run cost pennies rather than pounds, not to
        reconcile a bill.
        """
        return (self.input_tokens * 5.0 + self.output_tokens * 25.0) / 1_000_000


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """Which provider and model serve one role."""

    provider: str = "anthropic"
    model: str = "claude-opus-5"
    effort: str = "high"
    """``low``, ``medium``, ``high``, ``xhigh`` or ``max`` where supported."""

    max_tokens: int = 16000


@dataclass(frozen=True, slots=True)
class AiSettings:
    """Everything the user configures about AI, in one place.

    Keys are never stored here. They live in the OS credential store and are
    fetched by the adapter when a call is made, so a settings object can be
    logged, serialised or shown on screen without leaking anything.
    """

    roles: dict[ModelRole, ModelChoice] = field(default_factory=dict)
    max_attempts: int = 4
    """How many times the CAD loop may try before giving up."""

    spend_limit_usd: float = 5.0
    """Stop a run that has cost more than this. Zero means no limit."""

    def choice_for(self, role: ModelRole) -> ModelChoice:
        """The model configured for a role, or a sensible default."""
        if role in self.roles:
            return self.roles[role]
        return _DEFAULT_CHOICES[role]


_DEFAULT_CHOICES: dict[ModelRole, ModelChoice] = {
    # The hardest job in the app: writing correct parametric CAD.
    ModelRole.CAD_CODEGEN: ModelChoice(model="claude-opus-5", effort="high", max_tokens=16000),
    # Judging a render needs vision and care, but less depth.
    ModelRole.CRITIQUE: ModelChoice(model="claude-opus-5", effort="medium", max_tokens=4000),
    # Classification and extraction: frequent, cheap, easy.
    ModelRole.ROUTING: ModelChoice(model="claude-sonnet-5", effort="low", max_tokens=2000),
    ModelRole.NAMING: ModelChoice(model="claude-haiku-4-5", effort="low", max_tokens=500),
}


@runtime_checkable
class ChatProvider(Protocol):
    """Sending text to a model and getting text back."""

    def is_configured(self) -> bool:
        """Whether a usable credential is present."""
        ...

    def describe(self) -> str:
        """Which provider this is, for the settings panel."""
        ...

    def complete(self, conversation: Conversation, choice: ModelChoice) -> Result[Completion]:
        """Send a conversation and return the reply.

        A refusal, a rate limit or a network failure are expected outcomes and
        belong in the ``Result``, not in an exception.
        """
        ...


@runtime_checkable
class VisionProvider(ChatProvider, Protocol):
    """A provider that can also look at images.

    Separate from ``ChatProvider`` so an adapter that cannot see says so, rather
    than raising when the critique stage hands it a render.
    """

    def supports_vision(self) -> bool:
        """Whether images may be included in messages."""
        ...
