"""One plate with an AMS, or one plate per colour: which is worth it?

Rob asked this directly. A multi-colour print on a single plate swaps filament
at every colour change, and every swap purges material into the bin - the "poop".
The alternative is one plate per colour, which wastes nothing but needs a person
to change the plate and start the next print.

So it is a trade between **material and attention**, and which way it falls
depends entirely on the model. A logo with one stripe of a second colour swaps
twice. A model that alternates every layer swaps hundreds of times.

**Everything here is measured, not modelled.** The flush volume per change comes
from the slicer's own ``flush_volumes_matrix``; the number of changes comes from
counting tool changes in the toolpath; the times come from the slicer's own
estimates. Nothing is a constant somebody guessed, because a guessed constant
looks exactly like a measurement once it is on screen.

The one thing that cannot be measured is how long a plate change takes a person,
so it is a parameter with a stated default, and the comparison says which parts
of its answer rest on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modelpop.printing.toolpath import Toolpath

__all__ = [
    "PLATE_CHANGE_SECONDS",
    "PrintStrategy",
    "StrategyComparison",
    "compare_strategies",
]

# How long it takes a person to take a plate off, clear it, put it back and
# start the next print. A guess, and labelled as one everywhere it shows.
PLATE_CHANGE_SECONDS = 180.0

# PLA. The printer profile reports filament_density = 0, so this cannot be read
# from the file - see docs/09-virtual-print.md.
PLA_DENSITY_G_PER_CM3 = 1.24

# Below this the waste is not a decision. A spool is a kilogram, and a rate
# in grams per hour is noise when the numerator is a fraction of a gram.
NEGLIGIBLE_GRAMS = 5.0


@dataclass(frozen=True, slots=True)
class PrintStrategy:
    """One way of printing a multi-colour model, and what it costs."""

    name: str
    plates: int
    print_seconds: float
    """Time the printer spends printing, from the slicer's own estimate."""

    waste_mm3: float
    """Purged filament. Zero when there are no colour changes."""

    changes: int
    """Filament swaps during the print."""

    interventions: int
    """Times a person has to do something. Plate changes, mostly."""

    def total_seconds(self, plate_change_seconds: float = PLATE_CHANGE_SECONDS) -> float:
        """Printing plus the waiting a person causes."""
        return self.print_seconds + self.interventions * plate_change_seconds

    def waste_grams(self, density: float = PLA_DENSITY_G_PER_CM3) -> float:
        """The purged filament, weighed."""
        return self.waste_mm3 / 1000.0 * density

    def describe(self, plate_change_seconds: float = PLATE_CHANGE_SECONDS) -> str:
        """One line for the comparison table."""
        waste = f"{self.waste_grams():.1f} g wasted" if self.waste_mm3 > 0 else "nothing wasted"
        attention = (
            "unattended" if self.interventions == 0 else f"{self.interventions} plate change(s)"
        )
        return (
            f"{self.name}: {_duration(self.total_seconds(plate_change_seconds))}, "
            f"{waste}, {attention}"
        )


@dataclass(frozen=True, slots=True)
class StrategyComparison:
    """Two ways of printing the same model, side by side."""

    single_plate: PrintStrategy
    multi_plate: PrintStrategy
    plate_change_seconds: float = PLATE_CHANGE_SECONDS
    flush_was_stated: bool = True
    """False when the file gave no flush matrix, so the waste is unknown rather
    than zero. The difference matters: one is a measurement, the other is not."""

    @property
    def seconds_saved(self) -> float:
        """How much sooner the single plate finishes. Negative if it is slower."""
        return self.multi_plate.total_seconds(self.plate_change_seconds) - (
            self.single_plate.total_seconds(self.plate_change_seconds)
        )

    @property
    def grams_wasted(self) -> float:
        """What that speed costs in filament."""
        return self.single_plate.waste_grams() - self.multi_plate.waste_grams()

    @property
    def grams_per_hour_saved(self) -> float:
        """The exchange rate, which is the number the decision actually turns on.

        Zero when the single plate is not faster, because "grams per hour saved"
        means nothing if no hours were saved.
        """
        hours = self.seconds_saved / 3600.0
        if hours <= 0:
            return 0.0
        return self.grams_wasted / hours

    def recommendation(self) -> str:
        """Which to choose, and why - without pretending to be certain.

        The thresholds here are judgement, not measurement, and the sentence
        says what the judgement rests on so the user can disagree with it.
        """
        if not self.flush_was_stated:
            return (
                "The slicer did not state its purge volumes, so how much this wastes "
                "cannot be worked out from the file."
            )
        if self.single_plate.changes == 0:
            return "There are no colour changes, so both come to the same thing."
        if self.seconds_saved <= 0:
            return (
                "One plate per colour is faster as well as cleaner here. There is nothing to trade."
            )

        saved = _duration(self.seconds_saved)
        wasted = f"{self.grams_wasted:.1f} g"

        if self.grams_wasted < NEGLIGIBLE_GRAMS:
            # A rate means nothing when the absolute number is this small. Two
            # swaps on a one-kilogram spool is not a decision anybody needs to
            # weigh, whatever it works out to per hour.
            return (
                f"Use the AMS. It saves {saved} and wastes {wasted}, which is "
                "nothing off a spool - and it runs unattended."
            )

        if self.grams_per_hour_saved > 40:
            return (
                f"One plate per colour. The AMS would save {saved} but throw away "
                f"{wasted} doing it, which is a poor trade at "
                f"{self.grams_per_hour_saved:.0f} g an hour."
            )
        if self.grams_per_hour_saved < 10:
            return (
                f"Use the AMS. It saves {saved} for {wasted} of purge, and it runs "
                "unattended - the plate changes need you there."
            )
        return (
            f"Either. The AMS saves {saved} and wastes {wasted}; one plate per "
            "colour wastes nothing but needs you to change it "
            f"{self.multi_plate.interventions} time(s)."
        )

    def table(self) -> str:
        """Both options, for the panel."""
        lines = [
            self.single_plate.describe(self.plate_change_seconds),
            self.multi_plate.describe(self.plate_change_seconds),
            "",
            self.recommendation(),
        ]
        if self.multi_plate.interventions:
            lines.append(
                f"(A plate change is assumed to take {self.plate_change_seconds / 60:.0f} "
                "minutes of your time. That is an estimate, not a measurement.)"
            )
        return "\n".join(lines)


def compare_strategies(
    single_plate: Toolpath,
    per_colour: list[Toolpath],
    plate_change_seconds: float = PLATE_CHANGE_SECONDS,
) -> StrategyComparison:
    """Compare one multi-colour plate against one plate per colour.

    Both sides come from real slices of the same model. Comparing by slicing
    rather than by modelling is the whole point: the slicer already prices a
    tool change, and any constant invented here would look measured without
    being measured.

    Args:
        single_plate: the model sliced as one multi-colour plate.
        per_colour: the same model sliced once per colour.
        plate_change_seconds: how long a plate change takes a person.
    """
    waste, stated = _purge_volume(single_plate)

    single = PrintStrategy(
        name="One plate, AMS",
        plates=1,
        print_seconds=single_plate.stated_seconds,
        waste_mm3=waste,
        changes=len(single_plate.tool_changes),
        interventions=0,
    )

    multi = PrintStrategy(
        name="One plate per colour",
        plates=max(len(per_colour), 1),
        print_seconds=sum(path.stated_seconds for path in per_colour),
        waste_mm3=0.0,
        changes=0,
        # The first plate does not need a change; every one after it does.
        interventions=max(len(per_colour) - 1, 0),
    )

    return StrategyComparison(
        single_plate=single,
        multi_plate=multi,
        plate_change_seconds=plate_change_seconds,
        flush_was_stated=stated,
    )


def _purge_volume(toolpath: Toolpath) -> tuple[float, bool]:
    """How much filament the colour changes throw away, and whether we know.

    Returns zero and ``False`` when the file stated no flush matrix. That is
    different from zero waste, and collapsing the two would report a
    multi-colour print as wasting nothing.
    """
    if toolpath.flush.slots == 0:
        return 0.0, not toolpath.tool_changes

    total = sum(toolpath.flush.between(before, after) for before, after in toolpath.tool_changes)
    return total, True


def _duration(seconds: float) -> str:
    """A length of time, as a person would say it."""
    if seconds < 0:
        return f"-{_duration(-seconds)}"
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"
