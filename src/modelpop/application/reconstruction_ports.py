"""The port for turning a set of photographs into a model.

The other half of Phase 7. A single photograph goes to a generative model, which
*invents* a plausible back where it cannot see one; several photographs go here,
where the shape is **measured** rather than guessed. The two produce a mesh each
and are otherwise nothing alike, which is why they are separate ports rather
than one with a flag.

Reconstruction is minutes of work in seven stages across two external programs,
and the stages are wildly uneven - densification alone is two thirds of the wall
clock (`docs/research/spike-photogrammetry.md`). Progress is therefore reported
against measured weights, because an evenly-spaced bar appears to hang for a
minute in the middle, which is when people kill a job that is working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from modelpop.domain.mesh import Mesh
from modelpop.domain.result import Result
from modelpop.domain.units import Length

if TYPE_CHECKING:
    from collections.abc import Callable

    from modelpop.domain.photo_set import PhotoSet

__all__ = [
    "PhotoReconstructor",
    "Quality",
    "Reconstruction",
    "ReconstructionOptions",
    "Stage",
    "progress_through",
]

# Called with a fraction and a phrase, like the generator's. Reconstruction
# runs for minutes, so silence here reads as a hang rather than as work.
type Progress = Callable[[float, str], None]


class Stage(Enum):
    """The seven steps, in the order they run.

    The weight on each is its measured share of the wall clock, not a guess and
    not an even split. See the spike: the five COLMAP stages together are under
    five percent of a run.
    """

    FEATURES = ("Looking for detail in each photograph", 0.01)
    MATCHING = ("Finding the same detail in different photographs", 0.01)
    MAPPING = ("Working out where the camera was for each one", 0.04)
    UNDISTORTING = ("Straightening the lens out of the photographs", 0.01)
    HANDOVER = ("Handing the cameras to the mesher", 0.01)
    DENSIFYING = ("Filling in the surface between the matched points", 0.67)
    MESHING = ("Turning the points into a surface", 0.25)

    def __init__(self, description: str, weight: float) -> None:
        """Carry a phrase and a share of the total time."""
        self.description = description
        self.weight = weight

    @property
    def describe(self) -> str:
        """What to show while this stage runs."""
        return self.description


def progress_through(stage: Stage, within: float = 0.0) -> float:
    """How far along the whole run a point inside one stage is.

    ``within`` is how far through that stage, where it is known. Most stages
    cannot say, and report zero - which still moves the bar, because the stages
    before it are complete.
    """
    order = list(Stage)
    done = sum(s.weight for s in order[: order.index(stage)])
    return min(1.0, done + stage.weight * max(0.0, min(1.0, within)))


class Quality(Enum):
    """How hard to work, traded against how long it takes."""

    DRAFT = "draft"
    """Half resolution. Minutes, and enough to see whether the capture worked."""

    NORMAL = "normal"
    FINE = "fine"
    """Full resolution. Hours on a large set, and the detail a print can show."""

    @property
    def describe(self) -> str:
        """A phrase for the dialog."""
        return {
            Quality.DRAFT: "Draft - quick, and enough to tell whether the photographs were good",
            Quality.NORMAL: "Normal - the usual trade",
            Quality.FINE: "Fine - slow, and worth it only for a capture you trust",
        }[self]


@dataclass(frozen=True, slots=True)
class ReconstructionOptions:
    """What to ask of a reconstruction."""

    quality: Quality = Quality.NORMAL
    size: Length = field(default_factory=lambda: Length.mm(100))
    """How big to make the finished model, since a reconstruction has no scale."""

    size_was_measured: bool = False
    """True when the size came from a reference in shot rather than from a guess.

    Carried through so the note on the model can say which, because a measured
    size can be checked with calipers and a chosen one cannot - and the two look
    identical once the model is on screen.
    """

    timeout_seconds: float = 3600.0
    keep_largest_piece_only: bool = True
    """Throw away everything but the biggest connected lump.

    A capture of an object on a table is also a capture of the table. Off by
    request only: the one case where it is wrong is a subject that genuinely
    comes apart into pieces.
    """


@dataclass(frozen=True, slots=True)
class Reconstruction:
    """A model measured from photographs, and how well it went."""

    mesh: Mesh
    photos_given: int = 0
    photos_used: int = 0
    """How many photographs the solver could place.

    The single number that says whether a capture was good enough. Everything
    downstream is built from the pose graph, so a run that placed six of forty
    produced a confident-looking model of almost nothing.
    """

    seconds: float = 0.0
    discarded_fraction: float = 0.0
    """How much of the surface was thrown away as background."""

    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def coverage(self) -> float:
        """The share of the photographs that were placed."""
        return self.photos_used / self.photos_given if self.photos_given else 0.0

    @property
    def is_thin(self) -> bool:
        """Whether too few photographs were placed to trust the result."""
        return self.photos_given > 0 and self.coverage < 0.6

    @property
    def provenance(self) -> str:
        """A line recording where this shape came from.

        Kept in the document for the same reason the generator's is: six months
        later, "did I measure this or did a model invent it?" has no other
        answer - and here the answer is that it was measured, which is the
        stronger claim of the two.
        """
        return (
            f"Reconstructed from {self.photos_used} of {self.photos_given} "
            f"photographs in {self.seconds / 60:.1f} minutes"
        )

    def describe(self) -> str:
        """What to tell the user when it finishes."""
        said = [
            f"Built from {self.photos_used} of {self.photos_given} photographs, "
            f"{self.mesh.triangle_count:,} triangles."
        ]
        if self.is_thin:
            said.append(
                f"Only {self.coverage:.0%} of the photographs could be placed, so "
                "this is a model of what a few of them saw. More overlap between "
                "shots is what fixes that."
            )
        if self.discarded_fraction > 0.05:
            said.append(
                f"{self.discarded_fraction:.0%} was discarded as background - the "
                "table, the floor, whatever else was in shot."
            )
        return " ".join(said)


@runtime_checkable
class PhotoReconstructor(Protocol):
    """Turning several photographs into one measured mesh.

    Implementations must not raise. A capture that does not solve is the
    ordinary outcome of photographing a shiny or featureless object, and it
    belongs in the ``Result`` with a sentence about overlap and texture.
    """

    def is_available(self) -> bool:
        """Whether a reconstruction could start right now."""
        ...

    def describe(self) -> str:
        """What is installed and what is missing, specifically enough to fix."""
        ...

    def reconstruct(
        self,
        photos: PhotoSet,
        options: ReconstructionOptions | None = None,
        on_progress: Progress | None = None,
    ) -> Result[Reconstruction]:
        """Measure a shape from photographs."""
        ...
