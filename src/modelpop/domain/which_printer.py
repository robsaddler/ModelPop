"""Which printer the application thinks it is working with, and how it knows.

Three things can say what the printer is, and they are not equally trustworthy:

* **The printer itself**, when it answers. It knows what nozzle is fitted, and
  that is the one fact about a printer that changes without anybody telling the
  software - a swapped nozzle moves the thinnest printable wall from 0.84 mm to
  1.26, which the thin-wall warning and the thickening that answers it are both
  aimed at.
* **What it said last time**, when it does not answer. Better than a default,
  and worth marking as remembered rather than passing off as current.
* **The setting**, which is what the user chose and what stands when nothing
  else knows.

The build volume is deliberately *not* on that list. A printer does not report
how big its bed is - there is no such field in anything it sends - so the size
comes from knowing which model it is, and the model comes from a code the
printer announces or from the setting. Reading the profiles rather than asking
the machine is not a shortcut; it is the only route there is.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from modelpop.domain.printer import Nozzle, PrinterProfile

__all__ = ["WhichPrinter", "nearest_nozzle"]

# How long a reading stays worth calling current. Past this the label says it
# is remembered, because a figure from three weeks ago presented as live is
# worse than no figure: it is the one somebody would act on.
STALE_AFTER_HOURS = 12.0


def nearest_nozzle(millimetres: float) -> Nozzle | None:
    """The nozzle this diameter is, if it is one this application knows.

    Matched to the nearest size rather than exactly, because the printer
    reports a float and 0.39999 is a 0.4. Anything more than a twentieth of a
    millimetre from every known size is something else entirely and comes back
    as ``None`` rather than as the closest guess.
    """
    if millimetres <= 0.0:
        return None
    closest = min(Nozzle, key=lambda n: abs(n.value - millimetres))
    return closest if abs(closest.value - millimetres) <= 0.05 else None


@dataclass(frozen=True, slots=True)
class WhichPrinter:
    """The printer in use, and where each part of that came from."""

    profile: PrinterProfile = field(default_factory=PrinterProfile.p2s)
    """The model, its bed and the nozzle believed to be fitted."""

    chosen: str = ""
    """The model the user picked in Settings. Empty means they have not."""

    heard_at: datetime | None = None
    """When the printer last said anything about itself."""

    recognised: bool = False
    """Whether the printer named a model this application knows."""

    @property
    def is_live(self) -> bool:
        """Whether what is on show came from the printer recently enough."""
        if self.heard_at is None:
            return False
        return (datetime.now(UTC) - self.heard_at).total_seconds() < STALE_AFTER_HOURS * 3600

    @property
    def is_remembered(self) -> bool:
        """Whether this is the last thing the printer said rather than current."""
        return self.heard_at is not None and not self.is_live

    def describe(self) -> str:
        """The label above the build volume, over two lines.

        Says where the figures came from only when that is not "the printer,
        just now". A live reading needs no footnote; a remembered one does, and
        a model nobody has confirmed needs saying most of all.
        """
        profile = self.profile
        size = (
            f"{profile.build_width.format(places=0)} x {profile.build_depth.format(places=0)} x "
            f"{profile.build_height.format(places=0)} build volume"
        )
        return f"{profile.model}\n{size}{self._how_it_knows()}"

    def _how_it_knows(self) -> str:
        if self.is_live:
            return f" - {profile_nozzle(self.profile)} nozzle"
        if self.is_remembered:
            return f" - {profile_nozzle(self.profile)} nozzle, last seen {self._when()}"
        if self.chosen:
            return f" - {profile_nozzle(self.profile)} nozzle, as set"
        return f" - {profile_nozzle(self.profile)} nozzle, not confirmed with the printer"

    def _when(self) -> str:
        if self.heard_at is None:
            return "never"
        hours = (datetime.now(UTC) - self.heard_at).total_seconds() / 3600
        if hours < 48:
            return f"{hours:.0f} hours ago"
        return f"{hours / 24:.0f} days ago"

    def told_by_the_printer(
        self, model: PrinterProfile | None, nozzle: Nozzle | None, when: datetime | None = None
    ) -> WhichPrinter:
        """What we know after the printer has answered.

        The model only moves if the printer named one this application knows -
        an unrecognised code leaves the setting standing rather than replacing
        a real answer with a shrug. The nozzle moves whenever it is reported,
        because the printer is the only thing that can know it.
        """
        profile = model or self.profile
        if nozzle is not None:
            profile = replace(profile, nozzle=nozzle)
        return replace(
            self,
            profile=profile,
            heard_at=when or datetime.now(UTC),
            recognised=model is not None,
        )

    def chosen_in_settings(self, profile: PrinterProfile) -> WhichPrinter:
        """What we know after the user has picked a model.

        Their choice outranks a guess but keeps whatever nozzle the printer
        reported - which model it is and what is screwed into it are two
        different questions, and only one of them is being answered here.
        """
        return replace(
            self,
            profile=replace(profile, nozzle=self.profile.nozzle),
            chosen=profile.model,
        )


def profile_nozzle(profile: PrinterProfile) -> str:
    """The nozzle size, as it is written on the box."""
    return profile.nozzle.diameter.format()
