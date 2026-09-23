"""The port for turning a description or a photo into a mesh.

The other half of "turn my ideas into printable models". A parametric part comes
from :mod:`modelpop.application.generation_ports`; this is for the things that
are not parts - a dragon, a figurine, a shape nobody would describe with a box
and a fillet.

Kept well away from the rest of the application for one reason: the models that
do this need PyTorch and CUDA, several gigabytes of weights, and a Python
version the app itself does not run on. None of that may be allowed to become a
dependency of ModelPop, so it lives in its own environment and is reached
through a subprocess - exactly as the CAD kernel is, and for the same reasons.

An adapter that cannot run says so through ``is_available`` rather than failing
at the click. A machine with no GPU, no environment, or no weights downloaded
should have a disabled button and a sentence explaining it, not a traceback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from modelpop.domain.mesh import Mesh
from modelpop.domain.result import Result

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = [
    "Detail",
    "GeneratedMesh",
    "GenerationOptions",
    "MeshGenerator",
    "Progress",
]

# Called with a fraction and a phrase. Generation takes tens of seconds and a
# window with no sign of life reads as a crash.
type Progress = Callable[[float, str], None]


class Detail(Enum):
    """How much geometry to ask for.

    Named for the trade rather than a number, because the number that matters
    differs per model and the user is choosing between waiting and detail.
    """

    DRAFT = "draft"
    """Fast, rough. For seeing whether the idea is right at all."""

    STANDARD = "standard"
    FINE = "fine"
    """Slow, and often more triangles than a printer can use."""

    @property
    def describe(self) -> str:
        """A phrase for the dialog."""
        return {
            Detail.DRAFT: "quick and rough - is this the right idea?",
            Detail.STANDARD: "a usable model",
            Detail.FINE: "slow, and more detail than most prints need",
        }[self]


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """What to ask the generator for."""

    detail: Detail = Detail.STANDARD
    seed: int = 0
    """Zero means "pick one". A stated seed makes a run repeatable, which is the
    only way to iterate on a prompt rather than gamble on it."""

    remove_background: bool = True
    """For an image: cut the subject out first. Almost always wanted, because a
    photo's background otherwise becomes part of the model."""

    target_triangles: int = 200_000
    """A ceiling. These models happily produce millions, and a printer cannot
    use them - the slicer just takes longer to throw the detail away."""

    timeout_seconds: float = 600.0


@dataclass(frozen=True, slots=True)
class GeneratedMesh:
    """A mesh that came out of a generative model, and how."""

    mesh: Mesh
    model: str = ""
    """Which generator made it, for the document and for reproducing it."""

    seed: int = 0
    prompt: str = ""
    source_image: Path | None = None
    seconds: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)
    """Anything the run wants the user to know - what it simplified, what it
    guessed, what it could not do."""

    @property
    def provenance(self) -> str:
        """A line recording where this shape came from.

        Carried into the document, because six months later "did I make this or
        did a model?" is a question with no other answer.
        """
        source = (
            f'"{self.prompt}"'
            if self.prompt
            else (self.source_image.name if self.source_image else "an image")
        )
        return f"Generated from {source} by {self.model or 'a model'} (seed {self.seed})"


@runtime_checkable
class MeshGenerator(Protocol):
    """Turning a description or an image into a mesh."""

    def is_available(self) -> bool:
        """Whether this can run right now.

        False when the environment is not installed, the weights are missing or
        the GPU is busy. The caller disables the button rather than letting it
        fail at the click.
        """
        ...

    def describe(self) -> str:
        """Which model this is, and what state it is in.

        Shown in Settings, and it must be specific: "not installed" and "no GPU
        free" need different answers from the user.
        """
        ...

    def from_text(
        self,
        prompt: str,
        options: GenerationOptions | None = None,
        on_progress: Progress | None = None,
    ) -> Result[GeneratedMesh]:
        """Make a mesh from a description."""
        ...

    def from_image(
        self,
        image: Path,
        options: GenerationOptions | None = None,
        on_progress: Progress | None = None,
    ) -> Result[GeneratedMesh]:
        """Make a mesh from a single photo or drawing."""
        ...
