"""Reading the G-code back, to see whether it will actually print.

The naive fear about 3D printing is the nozzle hitting the model. In ordinary
layer-by-layer printing that cannot happen: the printer builds strictly bottom
up, so the nozzle is always at the height of the layer it is laying down and
there is nothing above it to hit.

The failure that *does* happen constantly is an **unsupported island** - a
region that begins in mid-air with nothing beneath it. It gets extruded into
nothing, drags, and becomes a bird's nest. That is what this module finds, by
reading the toolpath the slicer actually produced rather than guessing from the
mesh.

Deliberately geometric. Warping and curling are thermal effects; no toolpath
analysis predicts them, and claiming otherwise would be worse than silence.

Written against real Bambu Studio output, which decides three things that a
generic G-code parser gets wrong:

* **Extrusion is relative** (``M83``), so an extruding move is one with a
  positive ``E``, not one whose ``E`` exceeds the last. Reading it as absolute
  makes every move look like extrusion and every layer look solid.
* **Layers are marked** with ``; CHANGE_LAYER`` and ``; Z_HEIGHT:``. Splitting
  on Z changes instead produces a spurious layer for every travel z-hop.
* **Features are labelled** with ``; FEATURE:``, which is the only way to tell
  support material from the model it is holding up.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from modelpop.domain.printer import PrinterProfile
from modelpop.domain.readiness import Finding, Severity
from modelpop.domain.units import Length

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = [
    "GcodeVerification",
    "Layer",
    "LayerPreview",
    "ToolpathVerifier",
    "parse_layers",
    "verify_gcode",
]

# One cell per millimetre: fine enough to catch an island worth worrying about,
# coarse enough that a 300-layer print is analysed in well under a second.
_CELL_MM = 1.0

# Below this, a floating patch is a stray blob rather than a failure.
_MIN_ISLAND_MM2 = 6

# How far a cell may sit beyond supported material below and still be held up.
# About one nozzle width; beyond that it is bridging, not overhanging.
_SUPPORT_REACH_CELLS = 1

# The first layer rests on the plate, and the second is routinely a little
# wider than the first. Neither can float, so both are skipped.
_FIRST_CHECKED_LAYER = 2

_MAX_ISLAND_FINDINGS = 4


@dataclass(frozen=True, slots=True)
class Layer:
    """One printed layer: where material was actually laid down."""

    z: float
    points: NDArray[np.float64]
    """``(n, 2)`` XY positions sampled along the extrusion paths, in millimetres."""

    has_support: bool = False
    """Whether any of this layer's material is support rather than model."""

    @property
    def is_empty(self) -> bool:
        """Whether nothing was extruded at this height."""
        return len(self.points) == 0


@dataclass(frozen=True, slots=True)
class GcodeVerification:
    """What reading the toolpath revealed."""

    layer_count: int = 0
    findings: tuple[Finding, ...] = ()
    first_layer_area_mm2: float = 0.0
    unsupported_layers: int = 0
    tallest_z: float = 0.0
    sampled_points: int = 0

    @property
    def verdict(self) -> Severity:
        """The worst finding, or INFO when there are none."""
        return max((f.severity for f in self.findings), default=Severity.INFO)

    @property
    def was_read(self) -> bool:
        """Whether the file yielded anything at all."""
        return self.layer_count > 0

    def summary(self) -> str:
        """A line for the readiness panel."""
        if not self.was_read:
            return "The toolpath could not be read."
        if not self.findings:
            return f"Toolpath checked across {self.layer_count} layers: nothing wrong."
        return (
            f"Toolpath checked across {self.layer_count} layers: {len(self.findings)} finding(s)."
        )


def parse_layers(path: Path, max_points_per_layer: int = 40_000) -> list[Layer]:
    """Read a G-code file into per-layer extrusion points.

    Only extruding moves that also travel in XY count. A retraction changes
    ``E`` without laying anything down, and a travel move lays nothing down at
    all; counting either would make empty space look supported.

    Returns an empty list rather than raising when the file cannot be read: an
    extra check that could not run must not turn a successful slice into a
    failure.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    layers: list[Layer] = []
    points: list[tuple[float, float]] = []

    x = y = z = 0.0
    relative_e = True  # Bambu emits M83; corrected below if the file says otherwise
    last_e = 0.0
    saw_support = False

    def close_layer() -> None:
        nonlocal points, saw_support
        if points:
            layers.append(
                Layer(z=z, points=np.asarray(points, dtype=np.float64), has_support=saw_support)
            )
        points = []
        saw_support = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith(";"):
            marker = line.lstrip("; ").upper()
            if marker.startswith("CHANGE_LAYER"):
                close_layer()
            elif marker.startswith("Z_HEIGHT:"):
                with suppress(ValueError):
                    z = float(marker.split(":", 1)[1])
            elif marker.startswith("FEATURE:"):
                saw_support = saw_support or "SUPPORT" in marker
            continue

        if line.startswith("M83"):
            relative_e = True
            continue
        if line.startswith("M82"):
            relative_e = False
            continue

        if not line.startswith(("G0 ", "G1 ", "G0", "G1")):
            continue
        if line[:3] not in ("G0 ", "G1 ", "G0\t", "G1\t") and line[:2] not in ("G0", "G1"):
            continue

        new_x, new_y, e = x, y, None
        moved = False
        for token in line.split(";", 1)[0].split()[1:]:
            code = token[0].upper()
            try:
                number = float(token[1:])
            except ValueError:
                continue
            if code == "X":
                new_x, moved = number, True
            elif code == "Y":
                new_y, moved = number, True
            elif code == "Z":
                # Z appears on travel hops too; the layer markers are authoritative.
                pass
            elif code == "E":
                e = number

        if e is None:
            x, y = new_x, new_y
            continue

        extruding = e > 0 if relative_e else e > last_e
        if not relative_e:
            last_e = e

        if extruding and moved and len(points) < max_points_per_layer:
            points.extend(_sample(x, y, new_x, new_y))

        x, y = new_x, new_y

    close_layer()
    return layers


def _sample(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    """Points along an extrusion, spaced about one grid cell apart.

    Sampling matters: a long wall recorded only by its endpoints would leave
    everything between them looking unsupported.
    """
    distance = float(np.hypot(x1 - x0, y1 - y0))
    if distance <= _CELL_MM:
        return [(x1, y1)]
    steps = min(int(distance / _CELL_MM) + 1, 500)
    return [(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t) for t in np.linspace(0.0, 1.0, steps)]


def _occupancy(points: NDArray[np.float64], shape: tuple[int, int]) -> NDArray[np.bool_]:
    """Rasterise extrusion points onto a coarse grid."""
    grid = np.zeros(shape, dtype=bool)
    if len(points) == 0:
        return grid
    columns = (points[:, 0] / _CELL_MM).astype(np.int32)
    rows = (points[:, 1] / _CELL_MM).astype(np.int32)
    inside = (columns >= 0) & (columns < shape[1]) & (rows >= 0) & (rows < shape[0])
    grid[rows[inside], columns[inside]] = True
    return grid


def _dilate(grid: NDArray[np.bool_], reach: int) -> NDArray[np.bool_]:
    """Grow a grid outwards, so material slightly inboard still counts as support."""
    if reach <= 0:
        return grid
    grown = grid.copy()
    for shift in range(1, reach + 1):
        grown[shift:, :] |= grid[:-shift, :]
        grown[:-shift, :] |= grid[shift:, :]
        grown[:, shift:] |= grid[:, :-shift]
        grown[:, :-shift] |= grid[:, shift:]
    return grown


def verify_gcode(path: Path, printer: PrinterProfile | None = None) -> GcodeVerification:
    """Read a G-code file and report what would go wrong."""
    target = printer or PrinterProfile.p2s()
    layers = parse_layers(path)
    if not layers:
        return GcodeVerification()

    shape = (
        int(target.build_depth.millimetres / _CELL_MM) + 2,
        int(target.build_width.millimetres / _CELL_MM) + 2,
    )

    findings: list[Finding] = []
    unsupported_layers = 0
    below = _occupancy(layers[0].points, shape)

    for index, layer in enumerate(layers[1:], start=2):
        grid = _occupancy(layer.points, shape)
        if index >= _FIRST_CHECKED_LAYER:
            floating = int((grid & ~_dilate(below, _SUPPORT_REACH_CELLS)).sum())
            if floating >= _MIN_ISLAND_MM2:
                unsupported_layers += 1
                if len(findings) < _MAX_ISLAND_FINDINGS:
                    findings.append(
                        Finding(
                            "unsupported-island",
                            Severity.WARNING,
                            f"Layer {index} at {layer.z:.2f} mm starts about "
                            f"{floating} mm2 of material in mid-air.",
                            "Turn supports on, or re-orient the model so it is held up from below.",
                            fix_stage="orient",
                        )
                    )
        # material accumulates: a layer is held up by everything printed so far
        below = grid | below

    if unsupported_layers > _MAX_ISLAND_FINDINGS:
        findings.append(
            Finding(
                "unsupported-island",
                Severity.WARNING,
                f"{unsupported_layers} layers in total begin material in mid-air.",
                "This print needs supports.",
                fix_stage="orient",
            )
        )

    first_layer_area = float(_occupancy(layers[0].points, shape).sum())
    tallest = max(layer.z for layer in layers)
    findings.extend(_check_footprint(first_layer_area, tallest))

    return GcodeVerification(
        layer_count=len(layers),
        findings=tuple(findings),
        first_layer_area_mm2=first_layer_area,
        unsupported_layers=unsupported_layers,
        tallest_z=tallest,
        sampled_points=sum(len(layer.points) for layer in layers),
    )


def _check_footprint(area_mm2: float, height_mm: float) -> list[Finding]:
    """Flag a print that is tall on a small foot.

    The "it fell over at layer 40" failure, and one the user can prevent with a
    brim in five seconds once told.
    """
    if area_mm2 <= 0 or height_mm <= 0:
        return []
    radius = float(np.sqrt(area_mm2 / np.pi))  # equivalent radius, shape-agnostic
    if radius <= 0 or height_mm / radius <= 6.0:
        return []
    return [
        Finding(
            "first-layer-adhesion",
            Severity.WARNING,
            f"The print is {height_mm:.0f} mm tall on a {area_mm2:.0f} mm2 footprint.",
            "Add a brim, or re-orient it to sit on a wider face.",
            fix_stage="orient",
        )
    ]


@dataclass(frozen=True, slots=True)
class LayerPreview:
    """Everything a scrubable layer view needs, already in millimetres."""

    layers: tuple[Layer, ...] = ()

    @classmethod
    def read(cls, path: Path) -> LayerPreview:
        """Load a G-code file for previewing."""
        return cls(tuple(parse_layers(path)))

    @property
    def count(self) -> int:
        """How many layers there are."""
        return len(self.layers)

    def at(self, index: int) -> Layer | None:
        """One layer, or ``None`` when the index is out of range."""
        if 0 <= index < len(self.layers):
            return self.layers[index]
        return None

    def height_of(self, index: int) -> Length:
        """The Z height of a layer."""
        layer = self.at(index)
        return Length.mm(layer.z if layer else 0.0)


class ToolpathVerifier:
    """Satisfies the ``GcodeVerifier`` port."""

    def verify(self, gcode: Path, printer: PrinterProfile) -> tuple[Finding, ...]:
        """Findings about a toolpath, or nothing when it reads clean."""
        return verify_gcode(gcode, printer).findings

    def preview(self, gcode: Path) -> LayerPreview:
        """Load a toolpath for a scrubable layer view."""
        return LayerPreview.read(gcode)
