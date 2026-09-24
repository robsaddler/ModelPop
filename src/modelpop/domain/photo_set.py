"""What makes a set of photographs worth trying to reconstruct.

Reconstruction takes minutes and fails in ways that are only obvious afterwards,
so the cheap checks belong before it rather than after. All of them are
arithmetic over a list of file names: no image is opened, nothing is decoded,
and the whole thing is decidable in microseconds.

The distinction that matters here is between *refusing* and *warning*. Three
photographs cannot reconstruct anything and are refused. Twelve photographs will
probably produce something disappointing, which is worth saying and is not worth
blocking - the user may know something the rule does not, and a tool that
argues with people who are already holding the camera is a tool they stop using.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

__all__ = ["SUGGESTED_PHOTOS", "PhotoSet"]

# Structure from motion needs a third view to resolve the ambiguity two views
# leave. Below this there is nothing to attempt.
MIN_PHOTOS = 3

# Below this a reconstruction usually succeeds and usually disappoints: enough
# to solve the cameras, not enough to cover the object. Said, not enforced.
SUGGESTED_PHOTOS = 20

# Exhaustive matching compares every pair, so the work grows with the square of
# the count. Past this a capture is a video somebody dumped to frames, and it
# would run for hours.
MAX_PHOTOS = 300

# What the reconstruction tools will actually read.
READABLE = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"})


@dataclass(frozen=True, slots=True)
class PhotoSet:
    """The photographs to reconstruct from, and what is wrong with them."""

    photos: tuple[Path, ...] = ()

    @classmethod
    def of(cls, paths: Iterable[str | PathLike[str]]) -> PhotoSet:
        """Build a set from whatever the interface handed over.

        Sorted by name, because capture order is what the file names carry and
        a sequential matcher would otherwise be given the photographs shuffled.
        Anything that is not a readable image is dropped rather than refused:
        a folder of photographs routinely has a stray text file in it.
        """
        try:
            candidates = [Path(item) for item in paths]
        except TypeError:
            # A caller that handed over something that is not a list of paths at
            # all gets an empty set rather than an exception: this runs off a
            # file dialog, and the empty answer is already handled everywhere.
            return cls(())
        keep = [p for p in candidates if p.suffix.lower() in READABLE]
        return cls(tuple(sorted(set(keep), key=lambda p: p.name.lower())))

    def __len__(self) -> int:
        """How many usable photographs there are."""
        return len(self.photos)

    @property
    def problem(self) -> str | None:
        """Why this cannot be reconstructed at all, in words to act on."""
        if not self.photos:
            return "No photographs were chosen."
        if len(self.photos) < MIN_PHOTOS:
            return (
                f"{len(self.photos)} photograph(s) is not enough to work out where "
                f"the camera was. Reconstruction needs at least {MIN_PHOTOS}, and "
                f"about {SUGGESTED_PHOTOS} to be any good."
            )
        if len(self.photos) > MAX_PHOTOS:
            return (
                f"{len(self.photos)} photographs is more than this will attempt. "
                f"Every pair is compared with every other, so the work grows with "
                f"the square of the count. Use up to {MAX_PHOTOS}."
            )
        missing = [p.name for p in self.photos if not p.is_file()]
        if missing:
            return (
                f"{len(missing)} of the photographs are no longer there, "
                f"starting with {missing[0]}."
            )
        return None

    @property
    def advice(self) -> tuple[str, ...]:
        """What is worth saying before starting, without blocking anything.

        The user is the one who was in the room. A rule that refuses a capture
        it merely disapproves of is a rule people route around.
        """
        said: list[str] = []
        if MIN_PHOTOS <= len(self.photos) < SUGGESTED_PHOTOS:
            said.append(
                f"{len(self.photos)} photographs will probably reconstruct, but thinly. "
                f"About {SUGGESTED_PHOTOS} going right round the subject is where this "
                "starts being worth the wait."
            )
        if len({p.suffix.lower() for p in self.photos}) > 1:
            said.append(
                "The photographs are a mix of formats, which usually means they came "
                "from more than one camera. Reconstruction assumes one lens unless "
                "the images say otherwise."
            )
        return tuple(said)

    @property
    def is_usable(self) -> bool:
        """Whether a reconstruction could be attempted."""
        return self.problem is None

    def describe(self) -> str:
        """A line for the interface."""
        if not self.photos:
            return "No photographs chosen."
        where = self.photos[0].parent.name or str(self.photos[0].parent)
        return f"{len(self.photos)} photographs from {where}."
