"""Length and unit value types.

Scale is the single most common complaint about AI-generated models: the mesh is
the right shape and the wrong size. This module exists to make that mistake hard
to represent. A bare ``float`` never crosses a boundary in ModelPop; a
:class:`Length` does, and it always knows its unit.

Millimetres are the canonical internal unit because that is what slicers,
printers and 3MF all speak.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Self

__all__ = ["Length", "Unit"]


class Unit(Enum):
    """A unit of length, with its size in millimetres."""

    MILLIMETRE = ("mm", 1.0)
    CENTIMETRE = ("cm", 10.0)
    METRE = ("m", 1000.0)
    INCH = ("in", 25.4)
    FOOT = ("ft", 304.8)

    def __init__(self, symbol: str, millimetres: float) -> None:
        self.symbol = symbol
        self.millimetres = millimetres

    @classmethod
    def parse(cls, text: str) -> Unit:
        """Parse a unit from its symbol or name, case-insensitively.

        Accepts the symbol (``mm``, ``in``), the name (``inch``, ``inches``) or
        the enum name (``MILLIMETRE``).

        Raises:
            ValueError: if the text names no known unit.
        """
        cleaned = text.strip().lower()
        aliases: Final[dict[str, Unit]] = {
            "mm": cls.MILLIMETRE,
            "millimetre": cls.MILLIMETRE,
            "millimeter": cls.MILLIMETRE,
            "millimetres": cls.MILLIMETRE,
            "millimeters": cls.MILLIMETRE,
            "cm": cls.CENTIMETRE,
            "centimetre": cls.CENTIMETRE,
            "centimeter": cls.CENTIMETRE,
            "centimetres": cls.CENTIMETRE,
            "centimeters": cls.CENTIMETRE,
            "m": cls.METRE,
            "metre": cls.METRE,
            "meter": cls.METRE,
            "metres": cls.METRE,
            "meters": cls.METRE,
            "in": cls.INCH,
            "inch": cls.INCH,
            "inches": cls.INCH,
            '"': cls.INCH,
            "ft": cls.FOOT,
            "foot": cls.FOOT,
            "feet": cls.FOOT,
            "'": cls.FOOT,
        }
        if cleaned in aliases:
            return aliases[cleaned]
        raise ValueError(f"unknown unit: {text!r}")


@dataclass(frozen=True, slots=True, order=True)
class Length:
    """A length, stored canonically in millimetres.

    Two lengths are equal when they describe the same physical distance,
    regardless of the unit they were created in, so ``Length.inches(1)`` equals
    ``Length.mm(25.4)``.

    Construct via the named constructors rather than the initialiser, so the
    unit is always explicit at the call site.
    """

    millimetres: float

    # ------------------------------------------------------------------ build

    @classmethod
    def mm(cls, value: float) -> Self:
        """A length in millimetres."""
        return cls(float(value))

    @classmethod
    def cm(cls, value: float) -> Self:
        """A length in centimetres."""
        return cls(float(value) * Unit.CENTIMETRE.millimetres)

    @classmethod
    def metres(cls, value: float) -> Self:
        """A length in metres."""
        return cls(float(value) * Unit.METRE.millimetres)

    @classmethod
    def inches(cls, value: float) -> Self:
        """A length in inches."""
        return cls(float(value) * Unit.INCH.millimetres)

    @classmethod
    def feet(cls, value: float) -> Self:
        """A length in feet."""
        return cls(float(value) * Unit.FOOT.millimetres)

    @classmethod
    def of(cls, value: float, unit: Unit) -> Self:
        """A length in an arbitrary unit."""
        return cls(float(value) * unit.millimetres)

    @classmethod
    def parse(cls, text: str) -> Self:
        """Parse a length such as ``"6 inches"``, ``"150mm"`` or ``"2.5 cm"``.

        A bare number is interpreted as millimetres, matching the canonical unit.

        Raises:
            ValueError: if the text is not a number optionally followed by a unit.
        """
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("empty length")

        # split the leading numeric part from the trailing unit
        index = 0
        seen_digit = False
        while index < len(cleaned):
            char = cleaned[index]
            if char.isdigit():
                seen_digit = True
            elif char not in "+-.eE" or (char in "eE" and not seen_digit):
                break
            index += 1

        number_text, unit_text = cleaned[:index].strip(), cleaned[index:].strip()
        try:
            value = float(number_text)
        except ValueError as exc:
            raise ValueError(f"not a length: {text!r}") from exc

        unit = Unit.parse(unit_text) if unit_text else Unit.MILLIMETRE
        return cls.of(value, unit)

    # ------------------------------------------------------------------ read

    def to(self, unit: Unit) -> float:
        """This length expressed as a plain number in ``unit``."""
        return self.millimetres / unit.millimetres

    def format(self, unit: Unit = Unit.MILLIMETRE, places: int = 2) -> str:
        """A human-readable rendering, such as ``"25.40 mm"``."""
        return f"{self.to(unit):.{places}f} {unit.symbol}"

    # ------------------------------------------------------------------ maths

    def __add__(self, other: Length) -> Length:
        return Length(self.millimetres + other.millimetres)

    def __sub__(self, other: Length) -> Length:
        return Length(self.millimetres - other.millimetres)

    def __mul__(self, factor: float) -> Length:
        return Length(self.millimetres * factor)

    __rmul__ = __mul__

    def __truediv__(self, divisor: float) -> Length:
        if divisor == 0:
            raise ZeroDivisionError("cannot divide a length by zero")
        return Length(self.millimetres / divisor)

    def __neg__(self) -> Length:
        return Length(-self.millimetres)

    def __abs__(self) -> Length:
        return Length(abs(self.millimetres))

    def ratio(self, other: Length) -> float:
        """How many times ``other`` fits into this length."""
        if other.millimetres == 0:
            raise ZeroDivisionError("cannot take a ratio against a zero length")
        return self.millimetres / other.millimetres

    def is_close(self, other: Length, tolerance: Length | None = None) -> bool:
        """Whether two lengths agree within ``tolerance`` (default one micron)."""
        limit = tolerance if tolerance is not None else Length(1e-3)
        return abs(self.millimetres - other.millimetres) <= limit.millimetres

    def __str__(self) -> str:
        return self.format()
