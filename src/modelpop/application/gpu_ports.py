"""Exclusive use of the graphics card, as a port.

The card has 16 GB, and everything that wants it wants most of it: generating a
mesh from a picture, and now densifying a photogrammetry capture. Two at once
does not fail cleanly - it fails partway through, after the user has waited.

This exists as a port rather than as a class two adapters share because
``modelpop.generation`` and ``modelpop.vision`` are *peers* in the layering and
neither may import the other. That rule caught this the moment the second user
appeared, which is precisely what it is for: the lease is a shared concern, so
it belongs in the layer above both, and the composition root hands the one
implementation to everyone who needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

__all__ = ["GpuBusyError", "GpuLease"]


class GpuBusyError(RuntimeError):
    """The card is already in use.

    An exception rather than a ``Result`` because it is raised inside a context
    manager, where there is nothing to return. Callers turn it into a message
    at the boundary.
    """


@runtime_checkable
class GpuLease(Protocol):
    """Something that can grant exclusive use of the card."""

    @property
    def is_free(self) -> bool:
        """Whether work needing the card could start right now."""
        ...

    def describe(self) -> str:
        """Why the card is unavailable, in words for the user."""
        ...

    def held(self) -> AbstractContextManager[None]:
        """Hold the lease for the duration of a block.

        Raises:
            GpuBusyError: if something else already holds it.
        """
        ...
