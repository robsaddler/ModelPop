"""Finding models the application made earlier and never saved.

Generating a shape from a picture takes about a minute on this machine and the
result lands in a temporary file. If the application is closed, crashes, or -
as happened - leaves a model on screen that cannot be touched, that minute is
spent again for something that is still sitting on the disk.

So they are findable. No index and no database: the files name themselves, and
reading a directory listing is the whole mechanism. Anything else would be
another thing to keep in step with reality.

Pure: given a directory it answers with a list. What it finds is offered to the
user, never opened behind their back - these are old models, and which one is
wanted is not something to guess at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["EarlierModel", "found_in", "where_they_land"]

# What the generator writes, and what a reconstruction leaves behind.
PATTERNS = ("modelpop-generated-*", "modelpop-reconstructed-*", "modelpop-scene-*/*")

# Below this a file is a stub or a failed write rather than a model.
LEAST_INTERESTING_BYTES = 20_000

READABLE = {".glb", ".stl", ".obj", ".ply", ".3mf", ".off"}


@dataclass(frozen=True, slots=True)
class EarlierModel:
    """One model left behind by an earlier run."""

    path: Path
    made_at: datetime
    bytes: int

    @property
    def size(self) -> str:
        """How big it is, in the units a person reads."""
        megabytes = self.bytes / 1_048_576
        return f"{megabytes:.1f} MB" if megabytes >= 0.1 else f"{self.bytes / 1024:.0f} KB"

    @property
    def age(self) -> str:
        """How long ago it was made, roughly.

        Roughly on purpose: the useful question is "is this the one I made
        just now" and a timestamp to the second does not answer it any better
        than "12 minutes ago" does.
        """
        seconds = (datetime.now(UTC) - self.made_at).total_seconds()
        if seconds < 90:
            return "just now"
        if seconds < 5400:
            return f"{round(seconds / 60)} minutes ago"
        if seconds < 172_800:
            return f"{round(seconds / 3600)} hours ago"
        return f"{round(seconds / 86_400)} days ago"

    def describe(self) -> str:
        """One line, for a list the user picks from."""
        return f"{self.age} - {self.size} - {self.path.name}"


def where_they_land() -> Path:
    """The directory generated models are written to."""
    import tempfile

    return Path(tempfile.gettempdir())


def found_in(directory: Path, newest_first: bool = True) -> tuple[EarlierModel, ...]:
    """Every model left behind in a directory.

    Unreadable entries are skipped rather than raising: a temporary directory
    is shared with every other program on the machine, and one locked file is
    no reason to offer the user nothing.
    """
    found: list[EarlierModel] = []
    for pattern in PATTERNS:
        for path in _matching(directory, pattern):
            if path.suffix.lower() not in READABLE:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size < LEAST_INTERESTING_BYTES:
                continue
            found.append(
                EarlierModel(
                    path=path,
                    made_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                    bytes=stat.st_size,
                )
            )

    unique = {model.path: model for model in found}
    return tuple(sorted(unique.values(), key=lambda m: m.made_at, reverse=newest_first))


def _matching(directory: Path, pattern: str) -> list[Path]:
    """The files matching one pattern, or none if the directory will not read."""
    try:
        return [path for path in directory.glob(pattern) if path.is_file()]
    except OSError:
        return []
