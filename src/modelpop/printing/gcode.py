"""Reading the G-code back, to see whether it will actually print.

The naive fear about 3D printing is the nozzle hitting the model. In ordinary
layer-by-layer printing that cannot happen: the printer builds strictly bottom
up, so the nozzle is always at the height of the layer it is laying down and
there is nothing above it to hit. The one case where it *can* is sequential,
by-object printing, and that is checked in ``modelpop.printing.simulate``.

The failure that does happen constantly is an **unsupported island** - a region
that begins in mid-air with nothing beneath it. It gets extruded into nothing,
drags, and becomes a bird's nest. That is what this module finds, by reading the
toolpath the slicer actually produced rather than guessing from the mesh.

Deliberately geometric. Warping and curling are thermal effects; no toolpath
analysis predicts them, and claiming otherwise would be worse than silence.

The dialect lives in ``modelpop.printing.toolpath``, which is the only parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from modelpop.domain.printer import PrinterProfile
from modelpop.domain.readiness import Finding, Severity
from modelpop.domain.units import Length
from modelpop.printing.simulate import check_sequential_clearance
from modelpop.printing.toolpath import Layer, Toolpath

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

__all__ = [
    "GcodeVerification",
    "Layer",
    "LayerPreview",
    "ToolpathVerifier",
    "parse_layers",
    "verify_gcode",
    "verify_toolpath",
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

# A print taller than six times its equivalent footprint radius is the "it fell
# over at layer 40" case. Judgement, not physics, and set to avoid crying wolf.
_TIPPY_RATIO = 6.0

# Clearances around a P2S toolhead, for the sequential-print check.
_EXTRUDER_CLEARANCE_MM = 25.0
_GANTRY_CLEARANCE_MM = 180.0


@dataclass(frozen=True, slots=True)
class GcodeVerification:
    """What reading the toolpath revealed."""

    layer_count: int = 0
    findings: tuple[Finding, ...] = ()
    first_layer_area_mm2: float = 0.0
    unsupported_layers: int = 0
    tallest_z: float = 0.0
    sampled_points: int = 0
    toolpath: Toolpath = field(default_factory=Toolpath)
    """The parsed file, so a caller can play it back without re-reading it."""

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


def parse_layers(path: Path) -> list[Layer]:
    """Read a G-code file into per-layer extrusion points.

    Kept as a function because that is how the preview and the tests want it.
    The work happens in ``Toolpath``.
    """
    return list(Toolpath.read(path).layers)


def verify_gcode(path: Path, printer: PrinterProfile | None = None) -> GcodeVerification:
    """Read a G-code file and report what would go wrong."""
    return verify_toolpath(Toolpath.read(path), printer)


def verify_toolpath(toolpath: Toolpath, printer: PrinterProfile | None = None) -> GcodeVerification:
    """Check an already-parsed toolpath."""
    target = printer or PrinterProfile.p2s()
    layers = toolpath.layers
    if not layers:
        return GcodeVerification(toolpath=toolpath)

    shape = (
        int(target.build_depth.millimetres / _CELL_MM) + 2,
        int(target.build_width.millimetres / _CELL_MM) + 2,
    )

    findings, unsupported = _check_islands(layers, shape)

    first_layer_area = float(_occupancy(layers[0].points, shape).sum())
    tallest = toolpath.tallest_z
    findings.extend(_check_footprint(first_layer_area, tallest))
    findings.extend(
        check_sequential_clearance(toolpath, _GANTRY_CLEARANCE_MM, _EXTRUDER_CLEARANCE_MM)
    )

    return GcodeVerification(
        layer_count=len(layers),
        findings=tuple(findings),
        first_layer_area_mm2=first_layer_area,
        unsupported_layers=unsupported,
        tallest_z=tallest,
        sampled_points=sum(len(layer.points) for layer in layers),
        toolpath=toolpath,
    )


def _check_islands(layers: tuple[Layer, ...], shape: tuple[int, int]) -> tuple[list[Finding], int]:
    """Find material that starts in mid-air.

    Material accumulates: a layer is held up by everything printed so far, not
    only by the layer immediately below. That matters wherever a wall steps
    inwards and back out again.
    """
    findings: list[Finding] = []
    unsupported = 0
    below = _occupancy(layers[0].points, shape)

    for index, layer in enumerate(layers[1:], start=2):
        grid = _occupancy(layer.points, shape)
        if index >= _FIRST_CHECKED_LAYER:
            floating = int((grid & ~_dilate(below, _SUPPORT_REACH_CELLS)).sum())
            if floating >= _MIN_ISLAND_MM2:
                unsupported += 1
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
        below = grid | below

    if unsupported > _MAX_ISLAND_FINDINGS:
        findings.append(
            Finding(
                "unsupported-island",
                Severity.WARNING,
                f"{unsupported} layers in total begin material in mid-air.",
                "This print needs supports.",
                fix_stage="orient",
            )
        )
    return findings, unsupported


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


def _check_footprint(area_mm2: float, height_mm: float) -> list[Finding]:
    """Flag a print that is tall on a small foot.

    The "it fell over at layer 40" failure, and one the user can prevent with a
    brim in five seconds once told.
    """
    if area_mm2 <= 0 or height_mm <= 0:
        return []
    radius = float(np.sqrt(area_mm2 / np.pi))  # equivalent radius, shape-agnostic
    if radius <= 0 or height_mm / radius <= _TIPPY_RATIO:
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
        return cls(Toolpath.read(path).layers)

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
