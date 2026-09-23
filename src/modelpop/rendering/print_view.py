"""Drawing a print as it happens.

Turns a ``VirtualPrint`` into something the viewport can show: the toolpath laid
down so far, coloured by what the slicer called each move, and a marker where
the nozzle is.

Two decisions worth stating.

**Lines, not tubes.** A 300-layer print is a few hundred thousand segments, and
sweeping a circle along each one turns a scrub into a stutter. Drawn as lines
with a width, the whole print rebuilds fast enough to drag a slider through it.

**Coloured by feature, using the slicer's own names.** The preview and the
telemetry then agree, and the user learns the vocabulary the slicer already uses
rather than one this app invented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv

if TYPE_CHECKING:
    from modelpop.printing.simulate import Frame, VirtualPrint
    from modelpop.printing.toolpath import Segment

__all__ = [
    "DEFAULT_COLOUR",
    "FEATURE_COLOURS",
    "NOZZLE_COLOUR",
    "colour_of",
    "nozzle_marker",
    "progress_of",
    "to_lines",
]

# The slicer's own feature names, lower-cased. Anything unlisted falls back to
# the default rather than being hidden: an unknown feature is still material.
FEATURE_COLOURS: dict[str, str] = {
    "outer wall": "#6FA8DC",
    "inner wall": "#4A7BA7",
    "sparse infill": "#8E9BA8",
    "internal solid infill": "#7C8894",
    "top surface": "#9FC5E8",
    "bottom surface": "#9FC5E8",
    "bridge": "#E8834A",
    "internal bridge": "#C96F3E",
    "support": "#6B7B5E",
    "support interface": "#7F9170",
    "brim": "#5A6470",
    "skirt": "#5A6470",
    "prime tower": "#B09060",
    "gap infill": "#66727E",
}

DEFAULT_COLOUR = "#6FA8DC"
NOZZLE_COLOUR = "#E8C34A"


def colour_of(feature: str) -> str:
    """The colour for one of the slicer's feature names.

    Matched loosely, because the names carry qualifiers - "Internal Bridge
    infill", "Support interface" - and an exact lookup would silently grey out
    half the print.
    """
    name = feature.strip().lower()
    if not name:
        return DEFAULT_COLOUR
    if name in FEATURE_COLOURS:
        return FEATURE_COLOURS[name]

    # Longest match wins. "Internal Bridge infill" contains both "bridge" and
    # "internal bridge", and taking whichever came first in the table would
    # colour an internal bridge as an external one - a distinction that matters,
    # because one is a visible surface and the other is not.
    best = max((k for k in FEATURE_COLOURS if k in name), key=len, default="")
    return FEATURE_COLOURS[best] if best else DEFAULT_COLOUR


def to_lines(segments: tuple[Segment, ...]) -> pv.PolyData:
    """Turn extrusion moves into drawable lines.

    Every segment becomes a two-point line, with a scalar naming its feature so
    the viewport can colour it. Built in one array rather than per segment:
    appending a hundred thousand times is the difference between a scrub that
    drags and one that does not.
    """
    if not segments:
        return pv.PolyData()

    count = len(segments)
    points = np.empty((count * 2, 3), dtype=np.float64)
    for index, segment in enumerate(segments):
        points[index * 2] = segment.start
        points[index * 2 + 1] = segment.end

    # VTK wants each line prefixed with its point count: [2, a, b, 2, c, d, ...]
    connectivity = np.empty((count, 3), dtype=np.int64)
    connectivity[:, 0] = 2
    connectivity[:, 1] = np.arange(0, count * 2, 2)
    connectivity[:, 2] = connectivity[:, 1] + 1

    poly = pv.PolyData()
    poly.points = points
    poly.lines = connectivity.ravel()

    palette = _palette_for(segments)
    poly.cell_data["feature"] = palette
    poly.cell_data["height"] = np.array([s.start[2] for s in segments], dtype=np.float64)
    return poly


def _palette_for(segments: tuple[Segment, ...]) -> np.ndarray:
    """A colour per segment, as the RGB triples VTK draws directly.

    Per-cell colours rather than a lookup table, because the features are
    categories and interpolating between them would invent colours that mean
    nothing.
    """
    colours = np.empty((len(segments), 3), dtype=np.uint8)
    cache: dict[str, tuple[int, int, int]] = {}
    for index, segment in enumerate(segments):
        rgb = cache.get(segment.feature)
        if rgb is None:
            rgb = _rgb(colour_of(segment.feature))
            cache[segment.feature] = rgb
        colours[index] = rgb
    return colours


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    """A hex colour as three bytes."""
    value = hex_colour.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def nozzle_marker(frame: Frame, size: float = 3.0) -> pv.PolyData:
    """A small cone where the nozzle is, pointing down at the work.

    A marker rather than a model of the toolhead. The useful information is
    where material is being placed right now, and a detailed hotend would
    obscure exactly the part the user is trying to watch.
    """
    x, y, z = frame.position
    return pv.Cone(
        center=(x, y, z + size),
        direction=(0.0, 0.0, -1.0),
        height=size * 2,
        radius=size * 0.7,
        resolution=16,
    )


def progress_of(play: VirtualPrint, seconds: float) -> str:
    """A line for the scrubber: where the print is, in words.

    Time remaining rather than time elapsed, because that is the number people
    actually want from a printer.
    """
    if not play.can_play:
        return "Nothing to play."
    frame = play.at(seconds)
    left = max(play.total_seconds - frame.seconds, 0.0)
    minutes, secs = divmod(int(left), 60)
    hours, minutes = divmod(minutes, 60)
    remaining = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {secs:02d}s"
    layers = len(play.toolpath.layers)
    return (
        f"Layer {frame.layer + 1} of {layers} at {frame.z:.2f} mm "
        f"- {remaining} left - {frame.feature or 'moving'}"
    )
