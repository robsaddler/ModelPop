"""The virtual printer: what the head is doing at any moment of the print.

Watching a print build is the single most educational thing the app can show
someone new to printing. It makes "why did that fail" visible instead of
mysterious, and it costs almost nothing once the toolpath is already parsed for
the other checks.

**Where the clock comes from.** Bambu emits its own estimate twice: as a total
in the header, and as an ``M73 P<percent> R<minutes>`` ladder running through
the whole file. Interpolating that ladder is both more accurate than anything
reconstructed from feedrates and far simpler, because it already accounts for
acceleration, cooling waits, tool changes and every other thing a naive
kinematic model gets wrong. So the ladder is used when the file has one.

When it does not, the fallback is a trapezoidal velocity profile using the
machine limits the file states. That is honest but approximate, and the
simulation says which clock it used so nothing downstream has to guess.

**On nozzle collisions.** In ordinary layer-by-layer printing the nozzle cannot
strike the model: the printer builds strictly bottom-up, so the nozzle is always
at the height of the layer it is laying down and there is nothing above it. The
real collision case is *sequential* printing, where finished objects stand full
height while the head works on the next one. That is checked here, and only
there, because claiming to check a collision that physics already precludes
would be theatre.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from modelpop.domain.readiness import Finding, Severity
from modelpop.printing.toolpath import Segment, Toolpath

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["Clock", "Frame", "VirtualPrint"]


class Clock(Enum):
    """Where a simulation's timings came from, so a caller can say."""

    SLICER = "slicer"
    """Interpolated from the slicer's own ``M73`` ladder. Trust it."""

    ESTIMATED = "estimated"
    """Reconstructed from feedrates and machine limits. Approximate."""

    NONE = "none"
    """Nothing to simulate."""

    @property
    def describe(self) -> str:
        """A phrase for the status bar."""
        return {
            Clock.SLICER: "timed by the slicer",
            Clock.ESTIMATED: "estimated from the toolpath",
            Clock.NONE: "nothing to time",
        }[self]


@dataclass(frozen=True, slots=True)
class Frame:
    """Where the head is, and how far along, at one moment."""

    seconds: float
    position: tuple[float, float, float]
    layer: int
    segment: int
    """How many moves are complete. Everything before this has been laid down."""

    tool: int = 0
    feature: str = ""

    @property
    def z(self) -> float:
        """The height the head is working at, in millimetres."""
        return self.position[2]


@dataclass(frozen=True, slots=True)
class VirtualPrint:
    """A print that can be played back."""

    toolpath: Toolpath = field(default_factory=Toolpath)
    starts: tuple[float, ...] = ()
    """When each segment begins, in seconds from the start of the print."""

    total_seconds: float = 0.0
    clock: Clock = Clock.NONE

    @classmethod
    def read(cls, path: Path) -> VirtualPrint:
        """Load a G-code file for playback."""
        return cls.of(Toolpath.read(path))

    @classmethod
    def of(cls, toolpath: Toolpath) -> VirtualPrint:
        """Build a playback timeline for an already-parsed toolpath."""
        if not toolpath.segments:
            return cls(toolpath=toolpath)

        lengths = [segment.length for segment in toolpath.segments]
        if toolpath.stated_seconds > 0:
            starts, total = _timeline_from_slicer(toolpath, lengths)
            clock = Clock.SLICER
        else:
            starts, total = _timeline_from_motion(toolpath, lengths)
            clock = Clock.ESTIMATED

        return cls(toolpath=toolpath, starts=tuple(starts), total_seconds=total, clock=clock)

    # ------------------------------------------------------------- playback

    @property
    def can_play(self) -> bool:
        """Whether there is anything to watch."""
        return bool(self.starts) and self.total_seconds > 0

    def at(self, seconds: float) -> Frame:
        """Where the head is at a given moment.

        Clamped at both ends, so scrubbing past either edge of the timeline
        shows the first or last state rather than failing.
        """
        segments = self.toolpath.segments
        if not segments:
            return Frame(0.0, (0.0, 0.0, 0.0), -1, 0)

        moment = min(max(seconds, 0.0), self.total_seconds)
        index = max(0, bisect.bisect_right(self.starts, moment) - 1)
        segment = segments[index]

        started = self.starts[index]
        ends = self.starts[index + 1] if index + 1 < len(self.starts) else self.total_seconds
        span = max(ends - started, 1e-9)
        through = min(max((moment - started) / span, 0.0), 1.0)

        return Frame(
            seconds=moment,
            position=_between(segment.start, segment.end, through),
            layer=segment.layer,
            segment=index,
            tool=segment.tool,
            feature=segment.feature,
        )

    def at_fraction(self, fraction: float) -> Frame:
        """Where the head is, given a position on a 0 to 1 scrubber."""
        return self.at(self.total_seconds * min(max(fraction, 0.0), 1.0))

    def extruded_by(self, seconds: float) -> tuple[Segment, ...]:
        """Every move that has laid material down by a given moment.

        What the viewport draws. Travel moves are left out: drawing them is a
        different, noisier view, and the point of this one is to watch the
        object appear.
        """
        upto = self.at(seconds).segment
        return tuple(s for s in self.toolpath.segments[: upto + 1] if s.extrudes)

    def layer_reached(self, seconds: float) -> int:
        """Which layer is being printed at a given moment."""
        return self.at(seconds).layer

    def seconds_for_layer(self, layer: int) -> float:
        """When a layer starts, in seconds from the beginning of the print.

        What a "jump to layer 40" control needs.
        """
        for index, segment in enumerate(self.toolpath.segments):
            if segment.layer >= layer:
                return self.starts[index] if index < len(self.starts) else 0.0
        return self.total_seconds

    def describe(self) -> str:
        """A line for the status bar."""
        if not self.can_play:
            return "There is no toolpath to play."
        minutes, seconds = divmod(int(self.total_seconds), 60)
        hours, minutes = divmod(minutes, 60)
        clock = self.clock.describe
        length = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"
        return f"{length}, {len(self.toolpath.layers)} layers, {clock}."


# ------------------------------------------------------------------ timelines


def _timeline_from_slicer(toolpath: Toolpath, lengths: list[float]) -> tuple[list[float], float]:
    """Spread the slicer's own estimate across the moves.

    The ``M73`` ladder gives the remaining time at a hundred or so points, which
    is a coarse but *correct* shape: it already knows about acceleration,
    cooling waits and tool changes. Between two rungs, time is apportioned by
    distance travelled, which is the right behaviour inside a run of moves at
    similar speed and is all the resolution a playback needs.
    """
    total = toolpath.stated_seconds
    rungs = [(fraction, total - remaining) for fraction, remaining in toolpath.progress]
    rungs = [(f, max(0.0, t)) for f, t in rungs if 0.0 <= f <= 1.0]

    travelled = 0.0
    cumulative: list[float] = []
    for length in lengths:
        cumulative.append(travelled)
        travelled += length
    if travelled <= 0:
        return [0.0] * len(lengths), total

    if len(rungs) < 2:
        # no usable ladder: apportion the stated total by distance
        return [total * c / travelled for c in cumulative], total

    rungs.sort()
    fractions = [f for f, _ in rungs]
    elapsed = [t for _, t in rungs]

    starts: list[float] = []
    for distance in cumulative:
        done = distance / travelled
        index = min(bisect.bisect_right(fractions, done), len(fractions) - 1)
        low = max(index - 1, 0)
        span = fractions[index] - fractions[low]
        through = 0.0 if span <= 0 else (done - fractions[low]) / span
        starts.append(elapsed[low] + (elapsed[index] - elapsed[low]) * through)

    return starts, total


def _timeline_from_motion(toolpath: Toolpath, lengths: list[float]) -> tuple[list[float], float]:
    """Estimate timings from feedrates and machine limits.

    A trapezoidal profile per move: accelerate from the junction speed, cruise
    if there is room, decelerate to the next junction speed. Junction speed is
    the jerk limit, which is what a real firmware allows a corner to be taken
    at without stopping.

    Only used when the file carries no ``M73`` ladder. It is approximate and the
    simulation says so, which is better than presenting a reconstruction as
    though it were the printer's own number.
    """
    limits = toolpath.limits
    starts: list[float] = []
    elapsed = 0.0

    for segment, length in zip(toolpath.segments, lengths, strict=True):
        starts.append(elapsed)
        if length <= 0:
            continue

        cruise = min(segment.feedrate or limits.max_speed_xy, limits.max_speed_xy)
        accel = limits.accel_print if segment.extrudes else limits.accel_travel
        elapsed += _trapezoid_seconds(length, cruise, accel, limits.jerk_xy)

    return starts, elapsed


def _trapezoid_seconds(length: float, cruise: float, accel: float, junction: float) -> float:
    """How long one move takes, accelerating in and out of a corner speed."""
    if cruise <= 0 or accel <= 0:
        return 0.0
    entry = min(junction, cruise)

    # distance needed to reach cruise from entry, and to fall back to it
    ramp = (cruise * cruise - entry * entry) / (2 * accel)
    if 2 * ramp >= length:
        # never reaches cruise: a triangle, peaking mid-move
        peak = math.sqrt(2 * accel * (length / 2) + entry * entry)
        return 2 * (peak - entry) / accel

    flat = length - 2 * ramp
    return 2 * (cruise - entry) / accel + flat / cruise


def _between(
    start: tuple[float, float, float], end: tuple[float, float, float], through: float
) -> tuple[float, float, float]:
    """A point part way along a move."""
    return (
        start[0] + (end[0] - start[0]) * through,
        start[1] + (end[1] - start[1]) * through,
        start[2] + (end[2] - start[2]) * through,
    )


# --------------------------------------------------------- sequential printing


def check_sequential_clearance(
    toolpath: Toolpath, gantry_clearance_mm: float, extruder_clearance_mm: float
) -> tuple[Finding, ...]:
    """Whether a by-object print would drive the head into a finished object.

    The one genuine collision case. When objects are printed one at a time to
    completion, a finished object stands at full height while the head works on
    the next, so both the nozzle and the gantry above it can strike it.

    Returns nothing for an ordinary print, where the objects are built together
    layer by layer and there is nothing standing up to hit.
    """
    if not _is_sequential(toolpath):
        return ()

    tallest = toolpath.tallest_z
    if tallest <= extruder_clearance_mm:
        return ()  # nothing stands high enough for the head to reach it

    if tallest > gantry_clearance_mm:
        return (
            Finding(
                "sequential-gantry-collision",
                Severity.BLOCKER,
                f"Printing one object at a time, but an object reaches {tallest:.0f} mm, "
                f"above the {gantry_clearance_mm:.0f} mm the gantry clears.",
                "Print all objects together, or shorten them below the gantry height.",
                fix_stage="orient",
            ),
        )

    return (
        Finding(
            "sequential-print",
            Severity.WARNING,
            f"Objects are printed one at a time and reach {tallest:.0f} mm, above the "
            f"{extruder_clearance_mm:.0f} mm of clearance under the toolhead.",
            "Check the slicer's own object spacing warning before starting this print.",
            fix_stage="orient",
        ),
    )


def _is_sequential(toolpath: Toolpath) -> bool:
    """Whether the file prints objects one at a time.

    Detected from the shape of the toolpath rather than a setting: in a
    by-object print the Z height returns to the first layer partway through,
    because the head starts a new object from the plate.
    """
    heights = [layer.z for layer in toolpath.layers]
    if len(heights) < 3:
        return False
    first = heights[0]
    restarts = sum(
        1 for index, z in enumerate(heights[1:], 1) if z <= first and heights[index - 1] > z
    )
    return restarts >= 1
