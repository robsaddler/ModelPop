"""Reading a G-code file into the moves the printer will actually make.

One parser, used by everything downstream: the island check, the layer preview
and the virtual printer. Two parsers over the same dialect would drift, and the
first one got the dialect wrong in three separate ways, so there is exactly one
place to be wrong now.

Written against real Bambu Studio output. What that output decides:

* **Extrusion is relative** (``M83``). An extruding move has a positive ``E``,
  not an ``E`` larger than the last one.
* **Layers are marked** with ``; CHANGE_LAYER`` and ``; Z_HEIGHT:``. Splitting
  on Z changes manufactures a layer for every travel z-hop.
* **Features are labelled** with ``; FEATURE:``, the only way to tell support
  material from the model it holds up.
* **The slicer states its own time estimate** as an ``M73 P<percent> R<minutes>``
  ladder through the file, and as a total in the header. That is a far better
  clock than anything reconstructed from feedrates, and it comes free.
* **Machine limits are stated** by ``M201``/``M203``/``M204``/``M205`` near the
  top, so the fallback clock does not need constants invented for it.
* **Flush volumes are stated** as ``; flush_volumes_matrix``, in cubic
  millimetres per filament-to-filament change. That is the number behind the
  waste in a multi-colour print.

Arcs (``G2``/``G3``) are interpolated even though Bambu's default profile does
not emit extruding ones - the arcs in a default file are z-hop helices with no
material. Arc fitting is a setting, and a parser that silently drops material
when someone turns it on is worse than one that handles a case it rarely sees.
"""

from __future__ import annotations

import math
import re
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = [
    "FlushMatrix",
    "Layer",
    "MachineLimits",
    "Segment",
    "Toolpath",
]

# How finely an arc is chopped up. One chord per degree is far finer than the
# millimetre grid anything downstream works on.
_ARC_STEP_DEGREES = 3.0

# Samples along a long straight extrusion. A wall recorded only by its endpoints
# leaves everything between them looking unsupported.
_SAMPLE_MM = 1.0
_MAX_SAMPLES = 500

_TIME_IN_HEADER = re.compile(
    r";\s*(?:total estimated time|model printing time)\s*:\s*(.+)", re.IGNORECASE
)
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*([dhms])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MachineLimits:
    """What the printer can physically do, in millimetres and seconds.

    Read from the file when it says, because the file is describing the machine
    it was sliced for. The defaults are a P2S, taken from real output rather
    than from a specification sheet.
    """

    max_speed_xy: float = 600.0
    max_speed_z: float = 20.0
    accel_print: float = 20_000.0
    accel_travel: float = 10_000.0
    jerk_xy: float = 9.0
    """The speed a corner may be taken at without slowing to a stop."""

    @classmethod
    def read(cls, lines: list[str]) -> MachineLimits:
        """Pull the limits out of the commands near the top of a file."""
        limits = cls()
        for line in lines:
            if line.startswith("M201 "):
                limits = limits._with_accel(line)
            elif line.startswith("M203 "):
                limits = limits._with_speed(line)
            elif line.startswith("M205 "):
                limits = limits._with_jerk(line)
            elif line.startswith("M204 P"):
                limits = limits._with_named_accel(line)
        return limits

    def _with_accel(self, line: str) -> MachineLimits:
        fields = _fields(line)
        x = fields.get("X")
        return self if x is None else _replace(self, accel_print=x)

    def _with_named_accel(self, line: str) -> MachineLimits:
        fields = _fields(line)
        printing, travel = fields.get("P"), fields.get("T")
        updated = self if printing is None else _replace(self, accel_print=printing)
        return updated if travel is None else _replace(updated, accel_travel=travel)

    def _with_speed(self, line: str) -> MachineLimits:
        fields = _fields(line)
        x, z = fields.get("X"), fields.get("Z")
        updated = self if x is None else _replace(self, max_speed_xy=x)
        return updated if z is None else _replace(updated, max_speed_z=z)

    def _with_jerk(self, line: str) -> MachineLimits:
        x = _fields(line).get("X")
        return self if x is None else _replace(self, jerk_xy=x)


def _replace(limits: MachineLimits, **changes: float) -> MachineLimits:
    """``dataclasses.replace`` without importing it for one call site."""
    values = {
        "max_speed_xy": limits.max_speed_xy,
        "max_speed_z": limits.max_speed_z,
        "accel_print": limits.accel_print,
        "accel_travel": limits.accel_travel,
        "jerk_xy": limits.jerk_xy,
        **changes,
    }
    return MachineLimits(**values)


@dataclass(frozen=True, slots=True)
class FlushMatrix:
    """How much filament is purged changing from one slot to another.

    Cubic millimetres, straight from ``; flush_volumes_matrix``. This is the
    "poop": the material a multi-colour print throws away every time the
    printer swaps filament, and the whole reason single-plate multi-colour
    trades material for time.
    """

    volumes: tuple[tuple[float, ...], ...] = ()

    @classmethod
    def read(cls, lines: list[str]) -> FlushMatrix:
        """Find the matrix in the settings block, if the file states one."""
        for line in lines:
            if "flush_volumes_matrix" not in line:
                continue
            _, _, values = line.partition("=")
            numbers = [float(v) for v in values.split(",") if _is_number(v)]
            side = math.isqrt(len(numbers))
            if side * side != len(numbers) or side == 0:
                return cls()
            return cls(tuple(tuple(numbers[r * side : (r + 1) * side]) for r in range(side)))
        return cls()

    @property
    def slots(self) -> int:
        """How many filament slots the matrix describes."""
        return len(self.volumes)

    def between(self, from_slot: int, to_slot: int) -> float:
        """Millimetres cubed purged changing between two slots.

        Zero for a change to the same slot, and zero when the file said
        nothing - guessing a purge volume would put an invented number in
        front of the user as though it were measured.
        """
        if from_slot == to_slot:
            return 0.0
        if not (0 <= from_slot < self.slots and 0 <= to_slot < self.slots):
            return 0.0
        return self.volumes[from_slot][to_slot]


@dataclass(frozen=True, slots=True)
class Segment:
    """One straight move the head makes.

    Arcs arrive here already chopped into chords, so everything downstream
    deals with straight lines only.
    """

    start: tuple[float, float, float]
    end: tuple[float, float, float]
    extrudes: bool
    layer: int
    """Index into :attr:`Toolpath.layers`, or -1 before the first layer."""

    feature: str = ""
    tool: int = 0
    feedrate: float = 0.0
    """Millimetres per second, as commanded."""

    @property
    def length(self) -> float:
        """How far the head travels, in millimetres."""
        return math.dist(self.start, self.end)

    @property
    def is_support(self) -> bool:
        """Whether this lays down support rather than model."""
        return "SUPPORT" in self.feature.upper()


@dataclass(frozen=True, slots=True)
class Layer:
    """One printed layer: where material was actually laid down."""

    z: float
    points: NDArray[np.float64]
    """``(n, 2)`` XY positions sampled along the extrusion paths, in millimetres."""

    has_support: bool = False
    """Whether any of this layer's material is support rather than model."""

    first_segment: int = 0
    last_segment: int = 0

    @property
    def is_empty(self) -> bool:
        """Whether nothing was extruded at this height."""
        return len(self.points) == 0


@dataclass(frozen=True, slots=True)
class Toolpath:
    """Everything a G-code file says about what the printer will do."""

    segments: tuple[Segment, ...] = ()
    layers: tuple[Layer, ...] = ()
    limits: MachineLimits = field(default_factory=MachineLimits)
    flush: FlushMatrix = field(default_factory=FlushMatrix)
    stated_seconds: float = 0.0
    """The slicer's own total, from the header. Zero when it did not say."""

    progress: tuple[tuple[float, float], ...] = ()
    """``(fraction done, seconds remaining)`` from the ``M73`` ladder."""

    tool_changes: tuple[tuple[int, int], ...] = ()
    """``(from slot, to slot)`` in order, for costing a multi-colour print."""

    filament_mm: float = 0.0
    """Length of filament consumed, as the header states it."""

    filament_diameter: float = 1.75

    @classmethod
    def read(cls, path: Path) -> Toolpath:
        """Parse a G-code file.

        Returns an empty toolpath rather than raising when the file cannot be
        read: an extra check that could not run must not turn a successful
        slice into a failure.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return cls()
        return cls.parse(text.splitlines())

    @classmethod
    def parse(cls, lines: list[str]) -> Toolpath:
        """Parse G-code already in memory."""
        return _Parser(lines).run()

    @property
    def was_read(self) -> bool:
        """Whether the file yielded anything at all."""
        return bool(self.layers)

    @property
    def tallest_z(self) -> float:
        """The height of the top layer, in millimetres."""
        return max((layer.z for layer in self.layers), default=0.0)

    @property
    def filament_mm3(self) -> float:
        """Volume of filament consumed, in cubic millimetres.

        Computed from the stated length and diameter rather than read from the
        header's volume line, which is labelled ``cm^3`` but carries cubic
        millimetres.
        """
        radius = self.filament_diameter / 2
        return self.filament_mm * math.pi * radius * radius

    def grams(self, density_g_per_cm3: float = 1.24) -> float:
        """Weight of filament consumed.

        The density is a parameter with a PLA default because Bambu's own P2S
        profile reports ``filament_density = 0``, which makes the weight line
        in the header read ``0.00`` for every print. Reading that field and
        believing it is how the figure ends up wrong.
        """
        return self.filament_mm3 / 1000.0 * density_g_per_cm3


# --------------------------------------------------------------------- parsing


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _fields(line: str) -> dict[str, float]:
    """The letter-number pairs on a command line, comment stripped."""
    found: dict[str, float] = {}
    for token in line.split(";", 1)[0].split()[1:]:
        if len(token) < 2:
            continue
        with suppress(ValueError):
            found[token[0].upper()] = float(token[1:])
    return found


def _seconds_from(text: str) -> float:
    """Read "12m 25s" or "1h 4m 12s" into seconds."""
    scale = {"d": 86_400.0, "h": 3_600.0, "m": 60.0, "s": 1.0}
    total = 0.0
    for amount, unit in _DURATION.findall(text):
        total += float(amount) * scale[unit.lower()]
    return total


class _Parser:
    """Walks a file once, building every view of it at the same time."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._segments: list[Segment] = []
        self._layers: list[Layer] = []
        self._points: list[tuple[float, float]] = []

        self._x = self._y = self._z = 0.0
        self._relative_e = True  # Bambu emits M83; corrected if the file says otherwise
        self._last_e = 0.0
        self._feedrate = 0.0
        self._feature = ""
        self._tool = 0
        self._layer_index = -1
        self._saw_support = False
        self._layer_start = 0

        self._progress: list[tuple[float, float]] = []
        self._tool_changes: list[tuple[int, int]] = []
        self._stated_seconds = 0.0
        self._filament_mm = 0.0
        self._diameter = 1.75

    def run(self) -> Toolpath:
        """Parse the whole file."""
        head = self._lines[:400]
        limits = MachineLimits.read(head)
        flush = FlushMatrix.read(self._lines[:600])

        for raw in self._lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(";"):
                self._comment(line)
            else:
                self._command(line)

        self._close_layer()
        return Toolpath(
            segments=tuple(self._segments),
            layers=tuple(self._layers),
            limits=limits,
            flush=flush,
            stated_seconds=self._stated_seconds,
            progress=tuple(self._progress),
            tool_changes=tuple(self._tool_changes),
            filament_mm=self._filament_mm,
            filament_diameter=self._diameter,
        )

    # ------------------------------------------------------------- comments

    def _comment(self, line: str) -> None:
        marker = line.lstrip("; ").upper()
        if marker.startswith("CHANGE_LAYER"):
            self._close_layer()
            self._layer_index += 1
            self._layer_start = len(self._segments)
        elif marker.startswith("Z_HEIGHT:"):
            with suppress(ValueError):
                self._z = float(marker.split(":", 1)[1])
        elif marker.startswith("FEATURE:"):
            self._feature = line.split(":", 1)[1].strip()
            self._saw_support = self._saw_support or "SUPPORT" in marker
        elif self._stated_seconds == 0.0:
            self._read_header_time(line)

        if "TOTAL FILAMENT LENGTH" in marker and self._filament_mm == 0.0:
            with suppress(ValueError):
                self._filament_mm = float(marker.split(":", 1)[1])
        elif marker.startswith("FILAMENT_DIAMETER"):
            with suppress(ValueError):
                self._diameter = float(marker.split("=" if "=" in marker else ":", 1)[1])

    def _read_header_time(self, line: str) -> None:
        match = _TIME_IN_HEADER.match(line)
        if match is None:
            return
        # the line carries both the model time and the total; take the longer
        seconds = max(_seconds_from(part) for part in match.group(1).split(";"))
        if seconds > 0:
            self._stated_seconds = seconds

    # ------------------------------------------------------------- commands

    def _command(self, line: str) -> None:
        head = line.split(maxsplit=1)[0].upper()

        if head == "M83":
            self._relative_e = True
        elif head == "M82":
            self._relative_e = False
        elif head == "M73":
            self._read_progress(line)
        elif head.startswith("T") and head[1:].isdigit():
            self._change_tool(int(head[1:]))
        elif head in ("G0", "G1"):
            self._linear(line)
        elif head in ("G2", "G3"):
            self._arc(line, clockwise=head == "G2")

    def _read_progress(self, line: str) -> None:
        fields = _fields(line)
        percent, remaining = fields.get("P"), fields.get("R")
        if percent is None or remaining is None:
            return
        self._progress.append((percent / 100.0, remaining * 60.0))

    def _change_tool(self, slot: int) -> None:
        if slot != self._tool:
            self._tool_changes.append((self._tool, slot))
            self._tool = slot

    def _linear(self, line: str) -> None:
        fields = _fields(line)
        target_x = fields.get("X", self._x)
        target_y = fields.get("Y", self._y)
        moved = "X" in fields or "Y" in fields
        if "F" in fields:
            self._feedrate = fields["F"] / 60.0  # the file speaks mm/min

        extruding = self._extruding(fields.get("E"))
        if moved:
            self._emit(target_x, target_y, extruding)
        self._x, self._y = target_x, target_y

    def _arc(self, line: str, *, clockwise: bool) -> None:
        """Chop an arc into chords.

        Bambu's default profile only emits arcs as z-hop helices with no
        material, but arc fitting is a setting, and quietly losing extrusion
        when somebody turns it on would be the worst kind of bug.
        """
        fields = _fields(line)
        if "I" not in fields and "J" not in fields:
            return  # a helix with no XY target: the head ends where it began
        if "X" not in fields and "Y" not in fields:
            return

        if "F" in fields:
            self._feedrate = fields["F"] / 60.0
        extruding = self._extruding(fields.get("E"))

        centre_x = self._x + fields.get("I", 0.0)
        centre_y = self._y + fields.get("J", 0.0)
        end_x = fields.get("X", self._x)
        end_y = fields.get("Y", self._y)

        radius = math.hypot(self._x - centre_x, self._y - centre_y)
        if radius <= 0:
            self._emit(end_x, end_y, extruding)
            self._x, self._y = end_x, end_y
            return

        start_angle = math.atan2(self._y - centre_y, self._x - centre_x)
        end_angle = math.atan2(end_y - centre_y, end_x - centre_x)
        sweep = end_angle - start_angle
        if clockwise and sweep > 0:
            sweep -= 2 * math.pi
        elif not clockwise and sweep < 0:
            sweep += 2 * math.pi

        steps = max(2, int(abs(math.degrees(sweep)) / _ARC_STEP_DEGREES))
        for step in range(1, steps + 1):
            angle = start_angle + sweep * step / steps
            self._emit(
                centre_x + radius * math.cos(angle),
                centre_y + radius * math.sin(angle),
                extruding,
            )
        self._x, self._y = end_x, end_y

    def _extruding(self, e: float | None) -> bool:
        """Whether a move with this ``E`` lays material down."""
        if e is None:
            return False
        if self._relative_e:
            return e > 0
        extruding = e > self._last_e
        self._last_e = e
        return extruding

    # ---------------------------------------------------------------- emit

    def _emit(self, to_x: float, to_y: float, extruding: bool) -> None:
        """Record one straight move, and sample it if it lays material down."""
        self._segments.append(
            Segment(
                start=(self._x, self._y, self._z),
                end=(to_x, to_y, self._z),
                extrudes=extruding,
                layer=self._layer_index,
                feature=self._feature,
                tool=self._tool,
                feedrate=self._feedrate,
            )
        )
        if extruding:
            self._points.extend(_sample(self._x, self._y, to_x, to_y))
        self._x, self._y = to_x, to_y

    def _close_layer(self) -> None:
        if self._points:
            self._layers.append(
                Layer(
                    z=self._z,
                    points=np.asarray(self._points, dtype=np.float64),
                    has_support=self._saw_support,
                    first_segment=self._layer_start,
                    last_segment=len(self._segments),
                )
            )
        self._points = []
        self._saw_support = False


def _sample(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    """Points along an extrusion, spaced about one grid cell apart."""
    distance = math.hypot(x1 - x0, y1 - y0)
    if distance <= _SAMPLE_MM:
        return [(x1, y1)]
    steps = min(int(distance / _SAMPLE_MM) + 1, _MAX_SAMPLES)
    return [(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t) for t in np.linspace(0.0, 1.0, steps)]
