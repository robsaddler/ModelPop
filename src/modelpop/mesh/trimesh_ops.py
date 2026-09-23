"""Mesh operations backed by trimesh and manifold3d.

Satisfies the ``MeshOps`` port. trimesh does the inspection and cleanup;
manifold3d does the booleans, because it is the only free engine that is robust
on the kind of geometry AI generators produce.

Nothing here raises for bad geometry. Bad geometry is the normal case - it is
what this application exists to fix - so it comes back as a ``Failure``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
import trimesh

from modelpop.domain.mesh import Mesh
from modelpop.domain.readiness import MeshFacts
from modelpop.domain.result import Result, failure, success
from modelpop.domain.units import Length, Unit

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = ["TrimeshOps"]

# Below this, two vertices are the same point. Chosen to be far smaller than any
# printable feature but far larger than float noise.
_MERGE_TOLERANCE = 1e-4

# An island smaller than this fraction of the largest shell is debris, not model.
_ISLAND_FRACTION = 0.001


def _to_trimesh(mesh: Mesh) -> trimesh.Trimesh:
    """Convert a domain mesh into a trimesh, without validation or repair.

    The arrays are copied and forced C-contiguous. A domain mesh is immutable,
    so its arrays are read-only, and several native backends (notably the
    quadric decimator) reject non-contiguous or read-only input with an
    unhelpful signature error.
    """
    return trimesh.Trimesh(
        vertices=np.ascontiguousarray(mesh.vertices, dtype=np.float64).copy(),
        faces=np.ascontiguousarray(mesh.faces, dtype=np.int64).copy(),
        process=False,
        validate=False,
    )


def _from_trimesh(source: trimesh.Trimesh, unit: Unit) -> Mesh:
    """Convert a trimesh back into a domain mesh."""
    return Mesh(
        np.asarray(source.vertices, dtype=np.float64),
        np.asarray(source.faces, dtype=np.int32),
        unit,
    )


class TrimeshOps:
    """The default ``MeshOps`` implementation."""

    def describe(self) -> str:
        """Which libraries are in use, for diagnostics."""
        return f"trimesh {trimesh.__version__}"

    # ------------------------------------------------------------- inspection

    def inspect(self, mesh: Mesh, *, measure_walls: bool = True) -> MeshFacts:
        """Measure everything the readiness rules need, in one pass.

        Measuring once and handing the rules a record keeps them pure and fast,
        and stops the same expensive query being run by five different rules.

        Args:
            mesh: the mesh to measure.
            measure_walls: whether to ray-cast for wall thickness. It is the one
                genuinely expensive measurement here, so the viewport can skip
                it while the user is still dragging things around.
        """
        if mesh.is_empty:
            return MeshFacts(mesh=mesh, is_watertight=False, is_winding_consistent=True)

        body = _to_trimesh(mesh)
        scale = mesh.unit.millimetres

        try:
            shell_count = int(body.body_count)
        except Exception:
            shell_count = 1

        watertight = bool(body.is_watertight)
        # wall thickness needs a closed surface to cast rays through
        walls = thinnest_wall(mesh) if (measure_walls and watertight) else None

        return MeshFacts(
            mesh=mesh,
            is_watertight=watertight,
            is_winding_consistent=bool(body.is_winding_consistent),
            shell_count=shell_count,
            hole_count=self._hole_count(body),
            degenerate_face_count=self._degenerate_face_count(body),
            duplicate_vertex_count=self._duplicate_vertex_count(body),
            thinnest_wall=walls,
            bed_contact_area_mm2=self._bed_contact_area(body, scale),
        )

    @staticmethod
    def _hole_count(body: trimesh.Trimesh) -> int:
        """How many open edges the mesh has.

        In a closed surface every edge is shared by exactly two faces, so an
        edge used only once lies on the rim of a hole. Counting rim edges is
        cheap and tells the user what they need to know: something is open.

        This counts edges, not distinct loops. A loop count would need the rim
        edges stitched into cycles, which is more work than the message warrants.
        """
        try:
            if body.is_watertight:
                return 0
            boundary = trimesh.grouping.group_rows(body.edges_sorted, require_count=1)
            return len(boundary)
        except Exception:
            return 0

    @staticmethod
    def _degenerate_face_count(body: trimesh.Trimesh) -> int:
        """Triangles with effectively no area."""
        try:
            return int(np.count_nonzero(~body.nondegenerate_faces()))
        except Exception:
            return 0

    @staticmethod
    def _duplicate_vertex_count(body: trimesh.Trimesh) -> int:
        """Vertices that sit on top of another vertex."""
        try:
            merged = body.copy()
            merged.merge_vertices(merge_tex=True, merge_norm=True)
            return max(0, len(body.vertices) - len(merged.vertices))
        except Exception:
            return 0

    @staticmethod
    def _bed_contact_area(body: trimesh.Trimesh, scale: float) -> float:
        """Area of the faces lying flat on the lowest plane, in square millimetres.

        A proxy for bed adhesion: a tall model with almost no contact area is
        the classic "it fell over at layer 40" failure.
        """
        try:
            lowest = float(body.vertices[:, 2].min())
            tris = body.triangles
            on_bed = np.all(np.isclose(tris[:, :, 2], lowest, atol=1e-6), axis=1)
            if not on_bed.any():
                return 0.0
            return float(body.area_faces[on_bed].sum() * scale * scale)
        except Exception:
            return 0.0

    # ------------------------------------------------------------- processing

    def normalise(self, mesh: Mesh) -> Mesh:
        """Merge duplicate vertices, drop degenerate faces and tiny islands.

        Always succeeds: the worst case is that nothing changes.
        """
        if mesh.is_empty:
            return mesh
        try:
            body = _to_trimesh(mesh)
            body.merge_vertices(digits_vertex=int(-np.log10(_MERGE_TOLERANCE)))
            body.update_faces(body.nondegenerate_faces())
            body.update_faces(body.unique_faces())
            body.remove_unreferenced_vertices()
            body = self._drop_tiny_islands(body)
            if len(body.faces) == 0:
                return mesh
            return _from_trimesh(body, mesh.unit)
        except Exception:
            return mesh

    @staticmethod
    def _drop_tiny_islands(body: trimesh.Trimesh) -> trimesh.Trimesh:
        """Discard disconnected fragments that are debris rather than model."""
        try:
            pieces = body.split(only_watertight=False)
        except Exception:
            return body
        if len(pieces) <= 1:
            return body
        largest = max(len(p.faces) for p in pieces)
        kept = [p for p in pieces if len(p.faces) >= largest * _ISLAND_FRACTION]
        if not kept or len(kept) == len(pieces):
            return body
        return cast("trimesh.Trimesh", trimesh.util.concatenate(kept))

    def repair(self, mesh: Mesh) -> Result[Mesh]:
        """Make the mesh watertight and consistently wound.

        Escalates: cheap cleanup first, then hole filling, and only then the
        expensive voxel remesh, which always produces a closed result but
        rounds off sharp edges.

        Returns a failure rather than a mesh that is still broken. Everything
        downstream trusts this, so a false success is the worst possible outcome.
        """
        if mesh.is_empty:
            return failure("Nothing to repair", "the model has no geometry")

        cleaned = self.normalise(mesh)
        body = _to_trimesh(cleaned)

        try:
            body.fix_normals()
            trimesh.repair.fill_holes(body)
            trimesh.repair.fix_inversion(body)
            trimesh.repair.fix_winding(body)
        except Exception as exc:
            return failure("Repair failed", f"{type(exc).__name__}: {exc}")

        if body.is_watertight and body.is_winding_consistent:
            return success(_from_trimesh(body, mesh.unit))

        return self._voxel_remesh(cleaned)

    def _voxel_remesh(self, mesh: Mesh) -> Result[Mesh]:
        """Rebuild the surface from a voxel grid.

        Guarantees a closed result, at the cost of rounding sharp edges, so it
        is the last resort rather than the first move.
        """
        try:
            body = _to_trimesh(mesh)
            extent = float(np.ptp(body.vertices, axis=0).max())
            if extent <= 0:
                return failure("Repair failed", "the model has no extent")
            pitch = extent / 256.0
            voxels = body.voxelized(pitch=pitch).fill()
            rebuilt = voxels.marching_cubes
            rebuilt.merge_vertices()
            trimesh.repair.fix_normals(rebuilt)
            if not rebuilt.is_watertight:
                return failure(
                    "Could not make the model watertight",
                    "even a voxel rebuild left it open; the geometry may be badly broken",
                )
            return success(_from_trimesh(rebuilt, mesh.unit))
        except Exception as exc:
            return failure("Repair failed", f"{type(exc).__name__}: {exc}")

    def decimate(self, mesh: Mesh, target_triangles: int) -> Result[Mesh]:
        """Reduce the triangle count, preserving the silhouette."""
        if target_triangles < 4:
            return failure("Decimation target too low", "a closed mesh needs at least 4 triangles")
        if mesh.triangle_count <= target_triangles:
            return success(mesh)
        try:
            body = _to_trimesh(mesh)
            reduced = body.simplify_quadric_decimation(face_count=target_triangles)
            if reduced is None or len(reduced.faces) == 0:
                return failure("Decimation failed", "the result had no faces")
            return success(_from_trimesh(reduced, mesh.unit))
        except Exception as exc:
            return failure("Decimation failed", f"{type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------- boolean

    def union(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        """Boolean union."""
        return self._boolean(left, right, "union")

    def difference(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        """Boolean subtraction of ``right`` from ``left``."""
        return self._boolean(left, right, "difference")

    def intersection(self, left: Mesh, right: Mesh) -> Result[Mesh]:
        """Boolean intersection."""
        return self._boolean(left, right, "intersection")

    def _boolean(
        self,
        left: Mesh,
        right: Mesh,
        operation: Literal["union", "difference", "intersection"],
    ) -> Result[Mesh]:
        """Run a boolean through manifold3d, which is robust on real-world input."""
        if left.is_empty or right.is_empty:
            return failure(f"Cannot {operation}", "one of the meshes is empty")
        if left.unit is not right.unit:
            right = right.to_unit(left.unit)
        try:
            result: Any = trimesh.boolean.boolean_manifold(
                [_to_trimesh(left), _to_trimesh(right)], operation=operation
            )
        except Exception as exc:
            return failure(f"Boolean {operation} failed", f"{type(exc).__name__}: {exc}")

        if result is None or len(getattr(result, "faces", ())) == 0:
            return failure(
                f"Boolean {operation} produced nothing",
                "the shapes may not overlap in the way the operation expects",
            )
        return success(_from_trimesh(result, left.unit))


def thinnest_wall(mesh: Mesh, samples: int = 2000) -> Length | None:
    """Estimate the thinnest wall by ray casting from the surface inwards.

    Shoots a ray from each sampled point along the inward normal and measures
    the distance to the far side. Approximate by design: an exact answer needs a
    full medial-axis computation, and the user only needs to know *whether*
    their detail will survive the nozzle.

    Returns ``None`` when the mesh is unsuitable for sampling.
    """
    if mesh.is_empty or mesh.triangle_count < 4:
        return None
    try:
        body = _to_trimesh(mesh)
        if not body.is_watertight:
            return None
        points, face_indices = trimesh.sample.sample_surface(body, min(samples, 20_000))
        normals: NDArray[np.float64] = body.face_normals[face_indices]
        origins = points - normals * 1e-4
        locations, ray_indices, _ = body.ray.intersects_location(
            ray_origins=origins, ray_directions=-normals, multiple_hits=False
        )
        if len(locations) == 0:
            return None
        distances = np.linalg.norm(locations - origins[ray_indices], axis=1)
        distances = distances[distances > 1e-6]
        if distances.size == 0:
            return None
        # the 1st percentile rather than the minimum, so one stray ray through a
        # crevice does not condemn an otherwise sound model
        return Length.mm(float(np.percentile(distances, 1)) * mesh.unit.millimetres)
    except Exception:
        return None
