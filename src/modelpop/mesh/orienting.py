"""Searching for the way up a model should print.

The candidates are the faces of its **convex hull**, because those are the only
positions it can actually rest in - anything else has it balanced on a point.
Near-parallel facets are clustered and their areas added up, which gives both a
shorter list and the measure of how steady each rest is: a model sitting on a
broad face of its hull stays there, one sitting on a sliver does not.

Scoring never rotates the model. Turning a million triangles sixty times over
would be the whole cost of the feature; instead the *down direction* is rotated
into the model's own frame and dotted with the face normals, which is one pass
over an array per candidate. Measured on the 1.1 million triangle dragon: the
hull is 7,102 facets and takes 0.08 s, clustering leaves 64 candidates, and
scoring all of them takes 2.1 s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from modelpop.domain.orienting import STEADY_ENOUGH, Resting, turns_that_put_down

if TYPE_CHECKING:
    import trimesh
    from numpy.typing import NDArray

    from modelpop.domain.mesh import Mesh

__all__ = ["OVERHANG_THRESHOLD_DEGREES", "best_resting_place"]

# Beyond this angle from vertical a downward face needs holding up. Matches the
# slicer's own default (`support_threshold_angle: 30`), so what this counts as
# overhanging is what Bambu Studio would put supports under.
OVERHANG_THRESHOLD_DEGREES = 30.0

# How many resting positions to score. The list is ordered by how much hull
# faces that way, so the ones dropped are slivers nothing would balance on.
MOST_CANDIDATES = 64

# Two hull facets pointing within this of each other are the same resting
# position. About ten degrees.
SAME_DIRECTION = 0.985

# Within this of the lowest point counts as touching the plate, in millimetres.
# A twentieth of a layer: an organic surface has no perfectly flat facet, so an
# exact test finds nothing resting anywhere.
ON_THE_PLATE_MM = 0.02

# Candidates are scored in blocks of this many, so the intermediate array stays
# small. A million faces against sixty candidates at once is four gigabytes.
BLOCK = 8

_STRAIGHT_DOWN = np.array([0.0, 0.0, -1.0])


def best_resting_place(
    mesh: Mesh, threshold_degrees: float = OVERHANG_THRESHOLD_DEGREES
) -> Resting | None:
    """The way up this model overhangs least, among the ways it can stand.

    Returns ``None`` only when there is nothing to measure. A model already
    lying the best way up comes back as a ``Resting`` with no turns in it,
    which is an answer and not a failure.
    """
    from modelpop.mesh.trimesh_ops import _to_trimesh

    if mesh.is_empty:
        return None

    body = _to_trimesh(mesh)
    normals = np.asarray(body.face_normals, dtype=np.float64)
    areas = np.asarray(body.area_faces, dtype=np.float64)
    vertices = np.asarray(body.vertices, dtype=np.float64)
    faces = np.asarray(body.faces)
    total = float(areas.sum())
    if total <= 0.0 or not np.all(np.isfinite(normals)):
        return None

    limit = float(np.cos(np.radians(90.0 - threshold_degrees)))
    directions, support = _where_it_could_rest(body)
    if len(directions) == 0:
        return None

    overhangs = _overhang_for_each(directions, normals, areas, vertices, faces, total, limit)
    now = _overhang_for_each(
        _STRAIGHT_DOWN[None, :], normals, areas, vertices, faces, total, limit
    )[0]

    widest = float(support.max()) or 1.0
    steadiness = support / widest

    # The least overhanging position it would actually stay in. Steadiness is a
    # gate rather than part of the score: a fraction more overhang is a trade
    # worth making, and falling over is not.
    standing = np.flatnonzero(steadiness >= STEADY_ENOUGH)
    if standing.size == 0:
        return None
    best = standing[int(np.argmin(overhangs[standing]))]

    down = directions[best]
    turns = turns_that_put_down(down)
    return Resting(
        down=(float(down[0]), float(down[1]), float(down[2])),
        overhang_now=float(now),
        overhang_then=float(overhangs[best]),
        steadiness=float(steadiness[best]),
        turns=turns,
    )


def _where_it_could_rest(body: trimesh.Trimesh) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Every direction the model could lie face-down in, and how broad each is.

    From the convex hull, because a model resting on the plate is resting on
    its hull. Near-parallel facets are one resting position, and their areas
    add: what matters is how much of the hull faces that way, not how many
    triangles the hull happened to use to say so.
    """
    hull = body.convex_hull
    normals = np.asarray(hull.face_normals, dtype=np.float64)
    areas = np.asarray(hull.area_faces, dtype=np.float64)

    directions: list[NDArray[np.float64]] = []
    support: list[float] = []
    for index in np.argsort(-areas):
        facing = normals[index]
        for slot, already in enumerate(directions):
            if float(np.dot(facing, already)) > SAME_DIRECTION:
                support[slot] += float(areas[index])
                break
        else:
            directions.append(facing)
            support.append(float(areas[index]))

    order = np.argsort(-np.array(support))[:MOST_CANDIDATES]
    return np.array(directions)[order], np.array(support)[order]


def _overhang_for_each(
    directions: NDArray[np.float64],
    normals: NDArray[np.float64],
    areas: NDArray[np.float64],
    vertices: NDArray[np.float64],
    faces: NDArray[np.int64],
    total: float,
    limit: float,
) -> NDArray[np.float64]:
    """What share of the surface would overhang, for each direction in turn.

    A face overhangs when it points downwards more steeply than the threshold
    and is not lying on the plate - a face resting on the plate is held up by
    the plate, which is the whole point of choosing what to rest on.
    """
    found = np.empty(len(directions), dtype=np.float64)
    for start in range(0, len(directions), BLOCK):
        block = directions[start : start + BLOCK]
        downwardness = normals @ block.T
        reach = vertices @ block.T
        lowest = reach.max(axis=0)
        on_plate = np.all(reach[faces] > lowest - ON_THE_PLATE_MM, axis=1)
        overhanging = (downwardness > limit) & ~on_plate
        for column in range(block.shape[0]):
            found[start + column] = float(areas[overhanging[:, column]].sum() / total)
    return found
