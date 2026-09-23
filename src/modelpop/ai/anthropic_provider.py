"""Talking to Claude.

Satisfies ``VisionProvider``. The user brings their own key, so this adapter has
to be forgiving about where that key lives and honest when it is missing: an
unconfigured provider says so, rather than failing at import or on first use.

Written against the current API surface. Two points that a stale memory tends to
get wrong: thinking is configured as ``{"type": "adaptive"}`` with depth set by
``output_config.effort`` - ``budget_tokens`` is rejected outright on current
models - and a refusal arrives as a successful HTTP 200 with
``stop_reason == "refusal"``, so the stop reason must be checked before the
content is read.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from modelpop.application.ai_ports import (
    Completion,
    Conversation,
    Message,
    ModelChoice,
)
from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from modelpop.ai.secrets import SecretStore

__all__ = ["ANTHROPIC_KEY_NAME", "AnthropicProvider"]

ANTHROPIC_KEY_NAME = "ANTHROPIC_API_KEY"

# Models that take `thinking` and `effort`. Older ones reject both, so the
# request is built differently for them rather than failing at the API.
_THINKING_MODELS = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-fable-5",
    "claude-fable-5-1",
)


class AnthropicProvider:
    """A ``VisionProvider`` backed by the Anthropic SDK."""

    def __init__(self, secrets: SecretStore | None = None) -> None:
        """Create the provider.

        Args:
            secrets: where to find the API key. The default store checks the
                environment first, then the OS credential store.
        """
        if secrets is None:
            from modelpop.ai.secrets import default_store

            secrets = default_store()
        self._secrets = secrets
        self._client: Any = None

    # ---------------------------------------------------------- availability

    def is_configured(self) -> bool:
        """Whether an API key is available."""
        return bool(self._secrets.get(ANTHROPIC_KEY_NAME))

    def supports_vision(self) -> bool:
        """Claude reads images."""
        return True

    def describe(self) -> str:
        """Which provider this is, and whether it is ready."""
        if not self.is_configured():
            return "Anthropic (no API key set)"
        return "Anthropic"

    # ----------------------------------------------------------------- calls

    def complete(self, conversation: Conversation, choice: ModelChoice) -> Result[Completion]:
        """Send a conversation and return the reply.

        Every expected failure - no key, rate limit, refusal, network trouble -
        comes back as a ``Failure`` with something the user can act on.
        """
        client = self._connect()
        if client is None:
            return failure(
                "No Anthropic API key",
                "Add one in Settings, or set the ANTHROPIC_API_KEY environment variable.",
            )

        try:
            response = client.messages.create(**self._build_request(conversation, choice))
        except Exception as exc:
            return self._explain(exc)

        return success(self._read(response))

    # -------------------------------------------------------------- internals

    def _connect(self) -> Any:
        """Build the client, once, if a key is available."""
        if self._client is not None:
            return self._client

        key = self._secrets.get(ANTHROPIC_KEY_NAME)
        if not key:
            return None

        import anthropic

        self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def _build_request(self, conversation: Conversation, choice: ModelChoice) -> dict[str, Any]:
        """Assemble the request body."""
        request: dict[str, Any] = {
            "model": choice.model,
            "max_tokens": choice.max_tokens,
            "messages": [self._encode(message) for message in conversation.messages],
        }

        if conversation.system:
            # A cache breakpoint on the system prompt: it is the same on every
            # attempt of a generate-and-correct loop, and it is the largest
            # stable part of the request.
            request["system"] = [
                {
                    "type": "text",
                    "text": conversation.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        if any(choice.model.startswith(prefix) for prefix in _THINKING_MODELS):
            # Adaptive thinking with depth set by effort. `budget_tokens` is
            # rejected on these models; do not reintroduce it.
            request["thinking"] = {"type": "adaptive"}
            request["output_config"] = {"effort": choice.effort}

        return request

    @staticmethod
    def _encode(message: Message) -> dict[str, Any]:
        """Turn a domain message into the SDK's content blocks."""
        if not message.images:
            return {"role": message.role.value, "content": message.text}

        blocks: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    # the SDK wants a newline-free base64 string
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            }
            for media_type, data in message.images
        ]
        # Images go before the text: the question should follow what it is about.
        blocks.append({"type": "text", "text": message.text})
        return {"role": message.role.value, "content": blocks}

    @staticmethod
    def _read(response: Any) -> Completion:
        """Turn an SDK response into a completion.

        Checks ``stop_reason`` before reading content. A refusal is an HTTP 200
        with an empty or apologetic body, and treating it as a normal answer
        produces a baffling downstream failure.
        """
        stop_reason = str(getattr(response, "stop_reason", "") or "")
        refused = stop_reason == "refusal"

        category = ""
        details = getattr(response, "stop_details", None)
        if refused and details is not None:
            category = str(getattr(details, "category", "") or "")

        text = "".join(
            block.text
            for block in getattr(response, "content", [])
            if getattr(block, "type", "") == "text"
        )

        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cached_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            model=str(getattr(response, "model", "") or ""),
            stop_reason=stop_reason,
            refused=refused,
            refusal_category=category,
        )

    @staticmethod
    def _explain(exc: Exception) -> Result[Completion]:
        """Turn an SDK exception into something the user can act on.

        Matched by class name rather than by import, so this module does not
        need the SDK loaded to explain why it could not be used.
        """
        name = type(exc).__name__
        message = str(exc)

        explanations = {
            "AuthenticationError": (
                "The API key was rejected",
                "Check it in Settings. Keys begin with 'sk-ant-'.",
            ),
            "PermissionDeniedError": (
                "The API key lacks permission",
                "It may be scoped to a different workspace.",
            ),
            "RateLimitError": (
                "Rate limited by Anthropic",
                "Wait a moment and try again, or reduce how many attempts a run makes.",
            ),
            "NotFoundError": (
                "That model is not available",
                f"Check the model name in Settings. {message}",
            ),
            "BadRequestError": ("The request was rejected", message),
            "APIConnectionError": (
                "Could not reach Anthropic",
                "Check your internet connection.",
            ),
            "APITimeoutError": (
                "The request timed out",
                "The model may be taking a long time; try a lower effort setting.",
            ),
        }

        if name in explanations:
            reason, detail = explanations[name]
            return failure(reason, detail)

        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return failure("Anthropic had a server error", f"{status}: try again shortly")

        return failure("The model call failed", f"{name}: {message}")
