"""A ``Result`` type for failures that are expected rather than exceptional.

Geometry work fails constantly and predictably: a mesh is not manifold, a
generator declines, a slicer reports a warning. Those are outcomes, not bugs, and
raising on them turns ordinary control flow into exception handling.

The rule in ModelPop: **expected failures cross a port boundary as a**
:class:`Result`; exceptions are reserved for programmer error.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Never

__all__ = ["Failure", "Result", "Success", "failure", "success"]


@dataclass(frozen=True, slots=True)
class Success[T]:
    """A successful outcome carrying a value."""

    value: T

    @property
    def ok(self) -> bool:
        """Always ``True``."""
        return True

    def unwrap(self) -> T:
        """The value."""
        return self.value

    def unwrap_or[U](self, _default: U) -> T | U:
        """The value; the default is unused."""
        return self.value

    def map[U](self, fn: Callable[[T], U]) -> Result[U]:
        """Apply ``fn`` to the value."""
        return Success(fn(self.value))

    def and_then[U](self, fn: Callable[[T], Result[U]]) -> Result[U]:
        """Chain another fallible step."""
        return fn(self.value)

    @property
    def error(self) -> Never:
        """Always raises; a success has no error."""
        raise AttributeError("Success has no error")


@dataclass(frozen=True, slots=True)
class Failure:
    """A failed outcome carrying a human-readable reason.

    ``reason`` is shown to the user, so write it for them: say what went wrong
    and, where possible, what to do about it.
    """

    reason: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Always ``False``."""
        return False

    def unwrap(self) -> Never:
        """Always raises.

        Raises:
            ValueError: always, carrying the reason.
        """
        message = self.reason if not self.detail else f"{self.reason}: {self.detail}"
        raise ValueError(message)

    def unwrap_or[T](self, default: T) -> T:
        """The default, since there is no value."""
        return default

    def map(self, _fn: Callable[[object], object]) -> Failure:
        """A failure maps to itself."""
        return self

    def and_then(self, _fn: Callable[[object], object]) -> Failure:
        """A failure short-circuits the chain."""
        return self

    @property
    def error(self) -> str:
        """The reason, with detail appended when present."""
        return self.reason if not self.detail else f"{self.reason}: {self.detail}"


type Result[T] = Success[T] | Failure


def success[T](value: T) -> Success[T]:
    """Wrap a value as a successful result."""
    return Success(value)


def failure(reason: str, detail: str = "") -> Failure:
    """Describe an expected failure."""
    return Failure(reason, detail)
