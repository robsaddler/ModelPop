"""Hypothesis strategies for generating meshes.

Property-based testing is the highest-value technique for geometry, because the
failures that matter are the ones nobody thinks to write an example for. These
strategies deliberately include the degenerate cases that break kernels.
"""

import numpy as np
from hypothesis import strategies as st

from modelpop.domain import Mesh, Unit

# Coordinates in a range a printer might actually see, avoiding values where
# float comparison stops being meaningful.
coordinate = st.floats(min_value=-500, max_value=500, allow_nan=False, allow_infinity=False)


def unit_cube(size: float = 1.0, origin: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Mesh:
    """A closed, correctly wound axis-aligned box.

    Volume is exactly ``size ** 3``, which makes it a good fixture for asserting
    that volume-preserving operations really preserve volume.
    """
    ox, oy, oz = origin
    s = size
    vertices = np.array(
        [
            [ox, oy, oz],
            [ox + s, oy, oz],
            [ox + s, oy + s, oz],
            [ox, oy + s, oz],
            [ox, oy, oz + s],
            [ox + s, oy, oz + s],
            [ox + s, oy + s, oz + s],
            [ox, oy + s, oz + s],
        ],
        dtype=np.float64,
    )
    # outward-facing winding
    faces = np.array(
        [
            [0, 3, 2], [0, 2, 1],  # bottom
            [4, 5, 6], [4, 6, 7],  # top
            [0, 1, 5], [0, 5, 4],  # front
            [1, 2, 6], [1, 6, 5],  # right
            [2, 3, 7], [2, 7, 6],  # back
            [3, 0, 4], [3, 4, 7],  # left
        ],
        dtype=np.int32,
    )
    return Mesh(vertices, faces)


def tetrahedron(scale: float = 1.0) -> Mesh:
    """The smallest closed mesh there is: four vertices, four triangles."""
    vertices = np.array(
        [[0, 0, 0], [scale, 0, 0], [0, scale, 0], [0, 0, scale]], dtype=np.float64
    )
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int32)
    return Mesh(vertices, faces)


def single_triangle() -> Mesh:
    """An open mesh - one triangle, no volume."""
    return Mesh(
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64),
        np.array([[0, 1, 2]], dtype=np.int32),
    )


def degenerate_sliver() -> Mesh:
    """A triangle with effectively zero area - a classic kernel killer."""
    return Mesh(
        np.array([[0, 0, 0], [1, 0, 0], [0.5, 1e-12, 0]], dtype=np.float64),
        np.array([[0, 1, 2]], dtype=np.int32),
    )


def duplicate_vertices() -> Mesh:
    """Two coincident vertices, as produced by careless mesh generators."""
    return Mesh(
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0]], dtype=np.float64),
        np.array([[0, 1, 2], [3, 1, 2]], dtype=np.int32),
    )


@st.composite
def boxes(draw) -> Mesh:
    """A randomly sized and positioned box."""
    size = draw(st.floats(min_value=0.1, max_value=100))
    origin = (draw(coordinate), draw(coordinate), draw(coordinate))
    return unit_cube(size, origin)


@st.composite
def meshes(draw) -> Mesh:
    """Any of the closed fixtures, in any unit."""
    mesh = draw(st.sampled_from([unit_cube(), tetrahedron(), unit_cube(5.0)]))
    unit = draw(st.sampled_from(list(Unit)))
    return mesh.with_unit(unit)
