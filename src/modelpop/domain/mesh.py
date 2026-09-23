"""The immutable triangle mesh at the centre of the domain.

A :class:`Mesh` is a value: operations return a new mesh rather than mutating
one. That kills a whole class of threading bug, lets every pipeline stage be
cached by content hash, and makes property-based testing straightforward.

The arrays are numpy and are marked read-only. numpy is the one third-party
package the domain is allowed to import; see ``.importlinter`` for why.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

import numpy as np

from modelpop.domain.units import Length, Unit

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = ["BoundingBox", "Mesh"]


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """An axis-aligned box in millimetres."""

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def width(self) -> Length:
        """Extent along X."""
        return Length(self.max_x - self.min_x)

    @property
    def depth(self) -> Length:
        """Extent along Y."""
        return Length(self.max_y - self.min_y)

    @property
    def height(self) -> Length:
        """Extent along Z."""
        return Length(self.max_z - self.min_z)

    @property
    def centre(self) -> tuple[float, float, float]:
        """The midpoint of the box."""
        return (
            (self.min_x + self.max_x) / 2,
            (self.min_y + self.max_y) / 2,
            (self.min_z + self.max_z) / 2,
        )

    @property
    def largest_dimension(self) -> Length:
        """The longest of the three extents."""
        return max(self.width, self.depth, self.height)

    def fits_within(self, width: Length, depth: Length, height: Length) -> bool:
        """Whether the box fits inside the given envelope, without rotation."""
        return (
            self.width.millimetres <= width.millimetres
            and self.depth.millimetres <= depth.millimetres
            and self.height.millimetres <= height.millimetres
        )


def _read_only(array: NDArray[np.floating] | NDArray[np.integer]) -> None:
    """Mark an array immutable so a Mesh really is a value."""
    array.setflags(write=False)


@dataclass(frozen=True, eq=False, slots=True)
class Mesh:
    """An immutable indexed triangle mesh.

    Attributes:
        vertices: ``(n, 3)`` float64 positions, in ``unit``.
        faces: ``(m, 3)`` int32 indices into ``vertices``.
        unit: the unit the vertex coordinates are expressed in.

    Equality is by content, not identity, so two meshes built the same way are
    equal. ``eq=False`` on the dataclass is deliberate: the generated ``__eq__``
    would compare arrays elementwise and raise on the ambiguous truth value.
    """

    vertices: NDArray[np.float64]
    faces: NDArray[np.int32]
    unit: Unit = Unit.MILLIMETRE
    _hash: str = field(default="", compare=False, repr=False)

    def __post_init__(self) -> None:
        """Validate shapes and dtypes, then freeze the arrays."""
        vertices = np.asarray(self.vertices, dtype=np.float64)
        faces = np.asarray(self.faces, dtype=np.int32)

        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError(f"vertices must have shape (n, 3), got {vertices.shape}")
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError(f"faces must have shape (m, 3), got {faces.shape}")
        if len(faces) and (faces.min() < 0 or faces.max() >= len(vertices)):
            raise ValueError(
                f"face indices must lie in [0, {len(vertices)}), "
                f"got [{faces.min()}, {faces.max()}]"
            )
        if not np.isfinite(vertices).all():
            raise ValueError("vertices contain NaN or infinity")

        _read_only(vertices)
        _read_only(faces)
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "faces", faces)

    # ------------------------------------------------------------------ build

    @classmethod
    def empty(cls, unit: Unit = Unit.MILLIMETRE) -> Self:
        """A mesh with no geometry."""
        return cls(
            np.zeros((0, 3), dtype=np.float64),
            np.zeros((0, 3), dtype=np.int32),
            unit,
        )

    # ------------------------------------------------------------------ read

    @property
    def vertex_count(self) -> int:
        """How many vertices."""
        return len(self.vertices)

    @property
    def triangle_count(self) -> int:
        """How many triangles."""
        return len(self.faces)

    @property
    def is_empty(self) -> bool:
        """Whether the mesh has no triangles."""
        return self.triangle_count == 0

    @property
    def bounds(self) -> BoundingBox:
        """The axis-aligned bounding box, in millimetres.

        An empty mesh has a zero box at the origin.
        """
        if self.is_empty:
            return BoundingBox(0, 0, 0, 0, 0, 0)
        scale = self.unit.millimetres
        low = self.vertices.min(axis=0) * scale
        high = self.vertices.max(axis=0) * scale
        return BoundingBox(*(float(v) for v in low), *(float(v) for v in high))

    @property
    def volume(self) -> float:
        """Signed volume in cubic millimetres, by the divergence theorem.

        Meaningful only for a closed, consistently wound mesh. A negative result
        indicates inverted winding, which is itself a useful signal.

        Vertices are shifted to the bounding-box centre first. Volume is
        translation-invariant, so this changes nothing mathematically, but it
        matters a great deal numerically: without it, a small mesh far from the
        origin loses most of its significant digits to cancellation. A 0.1 mm
        box at x=36 was measured wrong by more than 100% before this shift.
        Property-based testing found that; example-based testing would not have.
        """
        if self.is_empty:
            return 0.0
        scale = self.unit.millimetres
        centre = (self.vertices.min(axis=0) + self.vertices.max(axis=0)) / 2
        tris = (self.vertices - centre)[self.faces] * scale
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)

    @property
    def surface_area(self) -> float:
        """Total surface area in square millimetres."""
        if self.is_empty:
            return 0.0
        scale = self.unit.millimetres
        tris = self.vertices[self.faces] * scale
        cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        return float(np.linalg.norm(cross, axis=1).sum() / 2.0)

    @property
    def content_hash(self) -> str:
        """A stable hash of the geometry, for caching and snapshot tests.

        Computed lazily and memoised. Coordinates are rounded to six decimal
        places first, so the hash survives insignificant floating-point drift.
        """
        if self._hash:
            return self._hash
        digest = hashlib.sha256()
        digest.update(self.unit.symbol.encode())
        digest.update(np.round(self.vertices, 6).tobytes())
        digest.update(self.faces.tobytes())
        value = digest.hexdigest()[:32]
        object.__setattr__(self, "_hash", value)
        return value

    # ------------------------------------------------------------------ derive

    def with_unit(self, unit: Unit) -> Mesh:
        """Reinterpret the coordinates as being in ``unit``, without scaling them.

        Use this when a file lied about its units. To convert, use
        :meth:`to_unit` instead.
        """
        return Mesh(self.vertices, self.faces, unit)

    def to_unit(self, unit: Unit) -> Mesh:
        """Convert the coordinates into ``unit``, preserving physical size."""
        if unit is self.unit:
            return self
        factor = self.unit.millimetres / unit.millimetres
        return Mesh(self.vertices * factor, self.faces, unit)

    def scaled(self, factor: float) -> Mesh:
        """Uniformly scale about the origin."""
        return Mesh(self.vertices * factor, self.faces, self.unit)

    def scaled_to_fit(self, largest: Length) -> Mesh:
        """Scale so the longest bounding-box dimension equals ``largest``.

        This is what "make it about six inches tall" means in practice.
        Returns the mesh unchanged if it is empty or degenerate.
        """
        current = self.bounds.largest_dimension
        if self.is_empty or current.millimetres <= 0:
            return self
        return self.scaled(largest.ratio(current))

    def translated(self, dx: float, dy: float, dz: float) -> Mesh:
        """Move the mesh by an offset expressed in the mesh's own unit."""
        return Mesh(self.vertices + np.array([dx, dy, dz]), self.faces, self.unit)

    def centred_on_origin(self) -> Mesh:
        """Move the bounding-box centre to the origin."""
        if self.is_empty:
            return self
        centre = (self.vertices.min(axis=0) + self.vertices.max(axis=0)) / 2
        return Mesh(self.vertices - centre, self.faces, self.unit)

    def dropped_to_bed(self) -> Mesh:
        """Move the mesh so its lowest point sits on z=0, centred in X and Y.

        This is the placement a slicer expects.
        """
        if self.is_empty:
            return self
        low = self.vertices.min(axis=0)
        high = self.vertices.max(axis=0)
        offset = np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])
        return Mesh(self.vertices - offset, self.faces, self.unit)

    # ------------------------------------------------------------------ dunder

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mesh):
            return NotImplemented
        return self.content_hash == other.content_hash

    def __hash__(self) -> int:
        return hash(self.content_hash)

    def __repr__(self) -> str:
        return (
            f"Mesh({self.vertex_count} vertices, {self.triangle_count} triangles, "
            f"{self.unit.symbol})"
        )
