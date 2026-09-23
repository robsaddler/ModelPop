"""Printer and material description.

The numbers here were read from the Bambu Studio 2.8 profiles installed on the
target machine, not from documentation. See ``docs/research/spike-bambu-cli.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from modelpop.domain.units import Length

__all__ = [
    "AmsUnit",
    "Filament",
    "Nozzle",
    "PrinterConnection",
    "PrinterProfile",
    "SupportStyle",
    "SupportType",
]


class Nozzle(Enum):
    """Nozzle diameters shipped for the P2S."""

    FINE = 0.2
    STANDARD = 0.4
    WIDE = 0.6
    EXTRA_WIDE = 0.8

    @property
    def diameter(self) -> Length:
        """The nozzle bore."""
        return Length.mm(self.value)

    @property
    def line_width(self) -> Length:
        """Typical extrusion width, a little wider than the bore."""
        return Length.mm(self.value * 1.05)

    @property
    def minimum_wall(self) -> Length:
        """The thinnest wall worth printing: two extrusion lines.

        Anything below this either disappears or prints as a fragile single
        line, and is the commonest reason detail vanishes from a generated model.
        """
        return self.line_width * 2


class SupportType(Enum):
    """How the slicer should generate supports.

    Values match the strings Bambu Studio expects in its process settings.
    """

    NONE = "none"
    TREE_AUTO = "tree(auto)"
    TREE_MANUAL = "tree(manual)"
    NORMAL_AUTO = "normal(auto)"
    NORMAL_MANUAL = "normal(manual)"


class SupportStyle(Enum):
    """Support flavour, as Bambu Studio names it."""

    DEFAULT = "default"
    GRID = "grid"
    SNUG = "snug"
    ORGANIC = "organic"


# Bambu prints an eight-character code on the printer's own screen. Anything
# else is a typo or a password from somewhere entirely different.
ACCESS_CODE_LENGTH = 8


@dataclass(frozen=True, slots=True)
class PrinterConnection:
    """How to reach a printer on the local network.

    The access code is a **secret**, and the whole reason this is a type rather
    than three loose strings. Holding it in a dataclass with a default repr
    would put it in every traceback, every log line that formats a job, and
    every crash report - and it is the credential that lets anyone on the
    network drive the printer. So ``__repr__`` and ``describe`` both hide it,
    and it is fetched from the credential store rather than stored in a
    project file.

    LAN mode only. The cloud route needs a Bambu account, a token refresh
    cycle and a server in the middle, all of which is the opposite of what
    this application is for.
    """

    host: str = ""
    serial: str = ""
    access_code: str = ""

    def __post_init__(self) -> None:
        """Trim whatever was typed. Spaces come with every copy and paste."""
        for name in ("host", "serial", "access_code"):
            object.__setattr__(self, name, str(getattr(self, name)).strip())

    def __repr__(self) -> str:
        """Everything but the secret.

        Deliberately not the generated repr. This object ends up inside jobs,
        results and exception messages, and any of those may be logged.
        """
        return f"PrinterConnection(host={self.host!r}, serial={self.serial!r}, access_code=...)"

    @property
    def is_complete(self) -> bool:
        """Whether there is enough here to try a connection."""
        return bool(self.host) and bool(self.serial) and bool(self.access_code)

    @property
    def problem(self) -> str | None:
        """What is missing or wrong, in words the user can act on."""
        if not self.host:
            return "The printer needs an address - its IP, from its network screen."
        if not self.serial:
            return "The printer needs its serial number, from the same screen."
        if not self.access_code:
            return "The printer needs its access code, shown on its network screen."
        if len(self.access_code) != ACCESS_CODE_LENGTH:
            return (
                f"An access code is {ACCESS_CODE_LENGTH} characters. "
                f"That one is {len(self.access_code)}."
            )
        return None

    def describe(self) -> str:
        """A line for the interface, with no secret in it."""
        if not self.host:
            return "No printer set up."
        return f"{self.serial or 'a printer'} at {self.host}"


@dataclass(frozen=True, slots=True)
class Filament:
    """A loaded material."""

    name: str = "Bambu PLA Basic"
    colour: str = "#FFFFFF"
    material: str = "PLA"

    @property
    def is_flexible(self) -> bool:
        """Whether the material needs gentler handling and no sharp retractions."""
        return self.material.upper() in {"TPU", "TPE"}


@dataclass(frozen=True, slots=True)
class AmsUnit:
    """An Automatic Material System with four slots.

    Needed for the multi-colour comparison in ``docs/09-virtual-print.md``:
    printing several colours on one plate costs a purge every tool change,
    and the only way to know whether that beats separate plates is to slice
    both and compare.
    """

    slots: tuple[Filament | None, ...] = (None, None, None, None)

    @property
    def loaded(self) -> tuple[Filament, ...]:
        """The filaments actually present."""
        return tuple(f for f in self.slots if f is not None)

    @property
    def colour_count(self) -> int:
        """How many distinct materials are available."""
        return len(self.loaded)


@dataclass(frozen=True, slots=True)
class PrinterProfile:
    """A printer, its envelope and what is loaded into it.

    Defaults describe the Bambu Lab P2S as configured on the target machine.
    """

    model: str = "Bambu Lab P2S"
    nozzle: Nozzle = Nozzle.STANDARD
    build_width: Length = field(default_factory=lambda: Length.mm(256))
    build_depth: Length = field(default_factory=lambda: Length.mm(256))
    build_height: Length = field(default_factory=lambda: Length.mm(256))
    layer_height: Length = field(default_factory=lambda: Length.mm(0.2))
    support_threshold_degrees: float = 30.0
    ams: AmsUnit | None = None
    default_filament: Filament = field(default_factory=Filament)

    @classmethod
    def p2s(cls, nozzle: Nozzle = Nozzle.STANDARD) -> PrinterProfile:
        """The target printer, with Bambu's own defaults."""
        return cls(nozzle=nozzle)

    @property
    def envelope(self) -> tuple[Length, Length, Length]:
        """Build volume as (width, depth, height)."""
        return (self.build_width, self.build_depth, self.build_height)

    @property
    def build_volume_mm3(self) -> float:
        """Total build volume in cubic millimetres."""
        return (
            self.build_width.millimetres
            * self.build_depth.millimetres
            * self.build_height.millimetres
        )

    @property
    def colour_count(self) -> int:
        """How many colours can be printed without a manual swap."""
        return self.ams.colour_count if self.ams else 1

    @property
    def bed_centre(self) -> tuple[float, float]:
        """Where a single object should be placed, in millimetres."""
        return (self.build_width.millimetres / 2, self.build_depth.millimetres / 2)

    def default_process_name(self) -> str:
        """The Bambu process profile matching this layer height and nozzle.

        Only the 0.4 mm nozzle names are certain; the others are constructed by
        the same pattern and should be checked against the installed profiles
        before being relied on.
        """
        height = f"{self.layer_height.millimetres:.2f}mm"
        if self.nozzle is Nozzle.STANDARD:
            return f"{height} Standard @BBL P2S"
        return f"{height} Standard @BBL P2S {self.nozzle.value} nozzle"
