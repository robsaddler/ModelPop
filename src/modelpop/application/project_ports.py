"""The port for saving and opening a parametric model.

A ModelPop project is its feature tree. There is no geometry in the file: the
shape is derived by replaying the tree, which is what makes the model
parametric and what makes the file small enough to read.

That has a consequence worth stating. **A project file is only as good as the
version that opens it.** A tree saved with an operation a later build renames
would not rebuild, so the format carries a version and the loader is required
to keep what it does not understand rather than dropping it - the user's work is
in that file even when this build cannot draw all of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from modelpop.domain.commands import Document
from modelpop.domain.result import Result

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ProjectStore", "SavedProject"]


@dataclass(frozen=True, slots=True)
class SavedProject:
    """A tree read back from disk, and what could not be read."""

    document: Document
    """Everything in the file, including features this build does not know."""

    unknown: tuple[str, ...] = ()
    """Operations this build cannot rebuild. Kept in the document regardless."""

    written_by: str = ""
    """Which version wrote it, for a message when something does not open."""

    @property
    def is_complete(self) -> bool:
        """Whether this build can rebuild everything in the file."""
        return not self.unknown


@runtime_checkable
class ProjectStore(Protocol):
    """Reading and writing a project file."""

    def save(self, document: Document, path: Path) -> Result[Path]:
        """Write a feature tree to disk."""
        ...

    def load(self, path: Path) -> Result[SavedProject]:
        """Read a feature tree back.

        A file that is corrupt, truncated or from the future is a ``Failure``,
        not an exception: opening someone's file is exactly where a crash is
        least forgivable.
        """
        ...
