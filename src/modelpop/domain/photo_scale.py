"""Recovering a real size from a photograph.

A picture has no scale. The generator works in a normalised box and hands back
a model one unit across, so until now the app picked a size, made the model
that big, and said out loud that the size was chosen rather than measured.

This is what a ruler in the shot is for. Two lines drawn on the photograph -
one along something whose length is known, one across the subject - are enough
to turn "one unit across" into millimetres, by the same similar-triangles
argument a person uses when they hold a coin next to something.

Deliberately no computer vision. Finding a ruler automatically is a research
problem with a failure mode that is silent and plausible: a model that comes
out 30% wrong looks entirely reasonable and only fails against calipers. Two
dragged lines are exact, take five seconds, and the user can see what was
measured. Automatic detection can come later *behind this same value*, because
everything downstream takes a ``PhotoScale`` and does not care who drew it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from modelpop.domain.units import Length

__all__ = [
    "MIN_LINE_PIXELS",
    "PhotoScale",
    "Reference",
    "length_of",
]

# A line shorter than this is almost certainly a stray click rather than a
# measurement. It is also where the error gets embarrassing: a two-pixel line
# read to the nearest pixel is 50% wrong before anything else happens.
MIN_LINE_PIXELS = 20.0

# How much longer the subject may be than the reference before the error in
# reading the reference is worth warning about. Measured against a 10 mm
# reference, a 400 mm subject multiplies every pixel of slop forty times.
COMFORTABLE_RATIO = 8.0


def length_of(start: tuple[float, float], end: tuple[float, float]) -> float:
    """How long a drawn line is, in pixels."""
    return math.hypot(end[0] - start[0], end[1] - start[1])


@dataclass(frozen=True, slots=True)
class Reference:
    """A line drawn along something whose real length is known.

    A ruler, a coin, a printed calibration mark, the long edge of a bank card.
    Anything flat, in the same plane as the subject, and measurable.
    """

    pixels: float
    real: Length

    @property
    def is_usable(self) -> bool:
        """Whether this is a measurement rather than a stray click."""
        return self.pixels >= MIN_LINE_PIXELS and self.real.millimetres > 0

    @property
    def mm_per_pixel(self) -> float:
        """The scale this reference establishes.

        Zero when the reference is unusable, so a caller that ignores
        ``is_usable`` gets an obviously wrong answer rather than a division by
        zero three layers down.
        """
        return self.real.millimetres / self.pixels if self.is_usable else 0.0


@dataclass(frozen=True, slots=True)
class PhotoScale:
    """What a photograph says the subject really measures.

    The honesty is the point. A size that came from here is *measured*, and the
    app says so; a size that did not is chosen, and the app says that instead.
    Anything printed from a measured size can be checked with calipers, which
    is the only test of this that means anything.
    """

    reference: Reference
    subject_pixels: float

    @property
    def is_usable(self) -> bool:
        """Whether both lines are long enough to mean anything."""
        return self.reference.is_usable and self.subject_pixels >= MIN_LINE_PIXELS

    @property
    def subject(self) -> Length:
        """How big the subject really is, across the line that was drawn."""
        return Length.mm(self.subject_pixels * self.reference.mm_per_pixel)

    @property
    def is_a_stretch(self) -> bool:
        """Whether the reference is small enough that the error will show.

        Not a refusal. Measuring a chair against a coin works; it is just worth
        saying that a pixel of slop on the coin becomes centimetres on the
        chair, because the alternative is a user who trusts the number.
        """
        if not self.is_usable:
            return False
        return self.subject_pixels / self.reference.pixels > COMFORTABLE_RATIO

    def describe(self) -> str:
        """What was measured, in the terms the user drew it."""
        if not self.is_usable:
            return "Draw a line along something of known length, then one across the subject."
        told = (
            f"{self.reference.real.format()} of reference measures "
            f"{self.reference.pixels:.0f} pixels, so the subject is "
            f"{self.subject.format()} across."
        )
        if self.is_a_stretch:
            told += (
                " The reference is small next to the subject, so a pixel either "
                "way moves the answer a long way - use something longer if you can."
            )
        return told
