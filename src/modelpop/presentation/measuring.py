"""Measuring between two points on a model.

The question everybody asks of a model they are about to print and nobody can
answer by looking: how wide is that boss, how deep is that recess, will that
peg go into that hole. The readiness panel gives the overall size; this gives
any other size.

A state machine with three states and no UI framework in it, so the whole
interaction - turn it on, click, click, read the answer, click again to start
over - is driven by a test with no display. The viewport supplies points in
millimetres and knows nothing about what is being measured; this knows what is
being measured and nothing about rays.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

__all__ = ["Measurement", "MeasuringTool", "Step"]

type Point = tuple[float, float, float]


class Step(Enum):
    """Where a measurement has got to."""

    OFF = "off"
    FIRST = "waiting for the first point"
    SECOND = "waiting for the second point"
    DONE = "measured"


@dataclass(frozen=True, slots=True)
class Measurement:
    """The distance between two picked points, and how it breaks down.

    The axis distances are given as well as the straight-line one because on a
    printed part they are usually what is wanted: "how tall" and "how far
    across" are different questions from "how far apart", and a diagonal
    answer to either of them is a quietly wrong answer.
    """

    start: Point
    end: Point

    @property
    def distance(self) -> float:
        """Straight-line distance in millimetres."""
        return math.dist(self.start, self.end)

    @property
    def across(self) -> float:
        """How far apart along X, in millimetres."""
        return abs(self.end[0] - self.start[0])

    @property
    def back(self) -> float:
        """How far apart along Y, in millimetres."""
        return abs(self.end[1] - self.start[1])

    @property
    def up(self) -> float:
        """How far apart along Z, in millimetres."""
        return abs(self.end[2] - self.start[2])

    def describe(self) -> str:
        """The answer, in the terms a printed part is usually asked about."""
        return (
            f"{self.distance:.2f} mm apart "
            f"({self.across:.2f} across, {self.back:.2f} back, {self.up:.2f} up)"
        )


class MeasuringTool:
    """Two clicks on a model, and the distance between them.

    Deliberately forgiving about clicks that miss. A click that lands on the
    build plate or on empty space is not a measurement and is *ignored* rather
    than resetting: a half-finished measurement thrown away because a click was
    a pixel wide of the part is the most annoying thing a tool like this can
    do.
    """

    def __init__(self) -> None:
        """Start switched off, with nothing measured."""
        self._on = False
        self._start: Point | None = None
        self._end: Point | None = None

    # -------------------------------------------------------------- switching

    @property
    def is_on(self) -> bool:
        """Whether clicks in the viewport are being taken as measurements."""
        return self._on

    def turn_on(self) -> None:
        """Start measuring, from nothing."""
        self._on = True
        self._start = None
        self._end = None

    def turn_off(self) -> None:
        """Stop measuring and forget what was measured."""
        self._on = False
        self._start = None
        self._end = None

    def start_again(self) -> None:
        """Keep measuring, but throw away the current pair of points."""
        if self._on:
            self._start = None
            self._end = None

    # -------------------------------------------------------------- measuring

    def picked(self, point: Point | None) -> bool:
        """Take a click. True when it moved the measurement on.

        ``None`` means the click missed the model. A third click, once a
        measurement is complete, starts a new one from that point - which is
        what somebody measuring several things in a row expects, and saves them
        reaching for a reset button between each.
        """
        if not self._on or point is None:
            return False

        if self._start is None or self._end is not None:
            self._start, self._end = point, None
        else:
            self._end = point
        return True

    @property
    def step(self) -> Step:
        """What the tool is waiting for."""
        if not self._on:
            return Step.OFF
        if self._start is None:
            return Step.FIRST
        return Step.DONE if self._end is not None else Step.SECOND

    @property
    def measurement(self) -> Measurement | None:
        """The finished measurement, if there is one."""
        if self._start is None or self._end is None:
            return None
        return Measurement(self._start, self._end)

    @property
    def points(self) -> tuple[Point, ...]:
        """The points picked so far, for the viewport to draw."""
        return tuple(point for point in (self._start, self._end) if point is not None)

    def describe(self) -> str:
        """What to show the user right now, whatever state it is in."""
        match self.step:
            case Step.OFF:
                return "Measuring is off."
            case Step.FIRST:
                return "Click a point on the model."
            case Step.SECOND:
                return "Click a second point."
            case Step.DONE:
                measured = self.measurement
                assert measured is not None
                return f"{measured.describe()}. Click again to measure something else."
