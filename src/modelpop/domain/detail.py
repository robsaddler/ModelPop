"""How much detail a print can actually carry, and what it takes to hold it.

The arithmetic behind detail rescue, kept apart from the baking itself because
these are the numbers that decide whether the operation is worth doing at all,
and they are decidable without a mesh, a texture or a kernel.

Two floors, both measured rather than looked up (`docs/research/spike-detail-rescue.md`):

* a bump **shallower than one layer** cannot appear in the slice - the bake
  changes the mesh and the G-code comes out identical;
* a bump **narrower than one extrusion line** cannot be laid down at all.

And one ceiling that has nothing to do with the printer: a mesh needs about two
vertices across a feature for that feature to exist in the geometry. A model
with ten thousand vertices simply cannot hold half-millimetre detail, however
good the texture is, which is why subdividing comes first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from modelpop.domain.printer import Nozzle

__all__ = ["DetailPlan", "plan_detail"]

# A feature needs a vertex either side of it to exist. Measured: a sphere whose
# mean edge is 1.13 mm holds features of about 2.3 mm and no less.
VERTICES_ACROSS_A_FEATURE = 2.0

# Subdividing quadruples the triangle count each time, so this is the difference
# between a second and a minute. Four passes is 256 times the geometry, which is
# past anything a printer can show.
MAX_SUBDIVISIONS = 4

# Beyond this the viewport stops being usable and the slicer slows to a crawl,
# and no amount of detail is worth a model nobody can turn round.
MAX_TRIANGLES = 2_000_000


@dataclass(frozen=True, slots=True)
class DetailPlan:
    """What a detail bake would do, worked out before anything is touched."""

    amplitude_mm: float
    """How far the surface moves, as it will actually be applied."""

    requested_mm: float
    """How far it was asked to move, before the printer's floor was applied."""

    subdivisions: int
    """How many times to quadruple the mesh before displacing it."""

    finest_feature_mm: float
    """The smallest thing the subdivided mesh could hold, in millimetres."""

    refused: str = ""
    """Why this cannot be done at all, when it cannot."""

    @property
    def is_worth_doing(self) -> bool:
        """Whether this would change the printed object rather than just the mesh."""
        return not self.refused

    @property
    def was_clamped(self) -> bool:
        """Whether the depth asked for was raised to something a printer can show."""
        return self.amplitude_mm > self.requested_mm + 1e-9

    def describe(self) -> str:
        """A line for the user, saying what will happen and what will not."""
        if self.refused:
            return self.refused

        said = f"Bake the texture {self.amplitude_mm:.2f} mm deep"
        if self.subdivisions:
            said += f", after subdividing {self.subdivisions} time"
            said += "s" if self.subdivisions > 1 else ""
        if self.was_clamped:
            said += (
                f". {self.requested_mm:.2f} mm is shallower than one layer, "
                "so it was deepened to the least this printer can show"
            )
        return said + "."


def plan_detail(
    requested_mm: float,
    triangle_count: int,
    largest_dimension_mm: float,
    nozzle: Nozzle = Nozzle.STANDARD,
    layer_height_mm: float = 0.2,
) -> DetailPlan:
    """Work out whether a detail bake would show, and what it would take.

    Args:
        requested_mm: how deep the user wants the detail.
        triangle_count: how much geometry there is to work with.
        largest_dimension_mm: how big the model is, which together with the
            triangle count says how fine its surface can be.
        nozzle: what will print it.
        layer_height_mm: how thick each layer is.

    Everything here is arithmetic on four numbers, which is the point: the
    question "would this change anything" has an answer before a single vertex
    is moved.
    """
    if triangle_count <= 0 or largest_dimension_mm <= 0:
        return DetailPlan(0.0, requested_mm, 0, 0.0, refused="There is no model to work on.")

    floor = max(layer_height_mm, 0.01)
    amplitude = max(float(requested_mm), floor)

    # Wanting a metre of relief on a 40 mm model is a typo, not a request.
    if amplitude > largest_dimension_mm / 4:
        return DetailPlan(
            amplitude,
            requested_mm,
            0,
            0.0,
            refused=(
                f"{amplitude:.1f} mm of relief on a {largest_dimension_mm:.0f} mm model "
                "would swallow the shape. Try something under "
                f"{largest_dimension_mm / 4:.1f} mm."
            ),
        )

    # The finest thing worth trying to hold: one extrusion line across.
    wanted_feature = nozzle.line_width.millimetres * VERTICES_ACROSS_A_FEATURE

    subdivisions = 0
    triangles = triangle_count
    feature = _finest_feature(triangles, largest_dimension_mm)
    while (
        feature > wanted_feature
        and subdivisions < MAX_SUBDIVISIONS
        and triangles * 4 <= MAX_TRIANGLES
    ):
        subdivisions += 1
        triangles *= 4
        feature = _finest_feature(triangles, largest_dimension_mm)

    return DetailPlan(
        amplitude_mm=amplitude,
        requested_mm=float(requested_mm),
        subdivisions=subdivisions,
        finest_feature_mm=feature,
    )


def _finest_feature(triangle_count: int, largest_dimension_mm: float) -> float:
    """The smallest feature a mesh of this density could hold, in millimetres.

    From the mean edge length, estimated by spreading the triangles over a
    surface the size of the model's bounding box. Approximate on purpose: the
    answer is used to decide *how many times to subdivide*, where being one step
    out costs a few milliseconds, and a precise figure would need the mesh.
    """
    area = 6.0 * largest_dimension_mm**2
    per_triangle = area / max(triangle_count, 1)
    edge = math.sqrt(per_triangle * 4 / math.sqrt(3))
    return edge * VERTICES_ACROSS_A_FEATURE
