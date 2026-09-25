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
from modelpop.domain.orienting import Resting
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

# The least a vertex normal may agree with one of its faces before the
# correction in `_reach` is capped. At a cosine of 0.3 the vertex already
# travels three times the distance its face gains; below that it is a spine or
# a crease, and letting it run would throw the tip clear of the model.
_LEAST_AGREEMENT = 0.3


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
            overhang_area_fraction=self._overhang_fraction(body),
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
    def _overhang_fraction(body: trimesh.Trimesh, threshold_degrees: float = 30.0) -> float:
        """Fraction of the surface that overhangs beyond the support threshold.

        A face needs support when it points downwards steeply enough that the
        layer below cannot hold it up. Measured as the angle between the face
        normal and straight down: at 0 degrees the face is perfectly horizontal
        and facing the bed, which is the worst case.

        Faces already lying on the bed are excluded - they rest on the plate,
        not on air.
        """
        try:
            normals = body.face_normals
            areas = body.area_faces
            total = float(areas.sum())
            if total <= 0:
                return 0.0

            # cos of the angle from straight down; 1.0 means facing the bed
            downwardness = -normals[:, 2]
            limit = float(np.cos(np.radians(90.0 - threshold_degrees)))
            overhanging = downwardness > limit

            lowest = float(body.vertices[:, 2].min())
            on_bed = np.all(np.isclose(body.triangles[:, :, 2], lowest, atol=1e-6), axis=1)
            return float(areas[overhanging & ~on_bed].sum() / total)
        except Exception:
            return 0.0

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

    def best_resting_place(self, mesh: Mesh) -> Resting | None:
        """Which way up this model would overhang least. See `mesh.orienting`."""
        from modelpop.mesh.orienting import best_resting_place

        try:
            return best_resting_place(mesh)
        except Exception:
            # A hull that will not compute is a reason to offer nothing, not a
            # reason to take the application down.
            return None

    def thicken(self, mesh: Mesh, by: Length) -> Result[Mesh]:
        """Grow every surface outwards by a distance, thickening thin walls.

        Each vertex moves along its own normal, so a wall of thickness *t*
        becomes *t* + 2 x ``by``: half the growth comes from each of its two
        faces. That is the whole trick, and it is why this can fix a thin wall
        without anybody being able to see it happen - the dragon's thinnest
        wall went from 0.68 mm to 0.89 mm while the model itself grew 0.23 mm
        across, a quarter of one percent.

        Along the *vertex* normals rather than the face normals, so neighbouring
        triangles stay joined. Moving faces independently would open the mesh
        along every edge, which is the obvious implementation and produces a
        cloud of disconnected triangles.

        The offset must stay small. Pushed further than the local radius of a
        concave feature the surface passes through itself, and what comes back
        is no longer a solid - so the result is checked and a mesh that stopped
        being watertight is refused rather than handed on. Everything
        downstream trusts this.
        """
        if mesh.is_empty:
            return failure("Nothing to thicken", "the model has no geometry")
        distance = by.millimetres / mesh.unit.millimetres
        if distance <= 0.0:
            return success(mesh)

        try:
            body = _to_trimesh(mesh)
            was_watertight = bool(body.is_watertight)
            normals = np.asarray(body.vertex_normals, dtype=np.float64)
            if normals.shape != (len(body.vertices), 3):
                return failure("Thickening failed", "the surface has no usable normals")
            moved = np.asarray(body.vertices, dtype=np.float64) + normals * _reach(body, distance)
            grown = trimesh.Trimesh(vertices=moved, faces=body.faces, process=False)
        except Exception as exc:
            return failure("Thickening failed", f"{type(exc).__name__}: {exc}")

        if was_watertight and not bool(grown.is_watertight):
            return failure(
                "Thickening failed",
                "growing the surface that far made it fold through itself.",
            )
        return success(_from_trimesh(grown, mesh.unit))

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

        # Then a real mesh repair, which keeps the surface. On a 1,132,190
        # triangle model from Thingiverse this closed it in twelve seconds and
        # gave back every triangle and the exact volume.
        mended = self._mend(cleaned)
        if mended.ok:
            return mended

        return self._voxel_remesh(cleaned)

    def _mend(self, mesh: Mesh) -> Result[Mesh]:
        """Close the surface without rebuilding it.

        MeshFix walks the boundaries and stitches them, so the geometry that
        was already right stays exactly as it was - which is the whole
        difference between this and the voxel rebuild below. Optional: the
        application runs without it and says so rather than failing.
        """
        try:
            import pymeshfix
        except ImportError:
            return failure(
                "Mesh repair is not installed",
                "pymeshfix is missing, so only the voxel rebuild is available.",
            )

        try:
            vertices, faces = pymeshfix.clean_from_arrays(
                np.asarray(mesh.vertices, dtype=np.float64),
                np.asarray(mesh.faces, dtype=np.int32),
            )
        except Exception as exc:
            return failure("Repair failed", f"{type(exc).__name__}: {exc}")

        if len(faces) == 0:
            return failure("Repair failed", "the repair removed everything")

        body = trimesh.Trimesh(vertices, faces, process=False)
        if not body.is_watertight:
            return failure(
                "Could not make the model watertight",
                "the surface could not be stitched closed",
            )
        trimesh.repair.fix_normals(body)
        return success(_from_trimesh(body, mesh.unit))

    def _voxel_remesh(self, mesh: Mesh) -> Result[Mesh]:
        """Rebuild the surface from a voxel grid.

        Guarantees a closed result and *loses detail doing it*: the surface is
        re-derived from a grid, so fine relief becomes visible banding. The
        last resort, after ``_mend`` has been tried, and never the first move.
        """
        try:
            body = _to_trimesh(mesh)
            extent = float(np.ptp(body.vertices, axis=0).max())
            if extent <= 0:
                return failure("Repair failed", "the model has no extent")
            pitch = extent / 256.0
            voxels = body.voxelized(pitch=pitch).fill()
            rebuilt = voxels.marching_cubes
            # Marching cubes answers in *grid indices*, not millimetres. The
            # grid's own transform is the way back, and without it the result
            # comes out a couple of hundred units across whatever the model
            # measured - a 7 mm dragon rebuilt as a 257 mm one, with a volume
            # forty-eight thousand times too big. Nobody saw it until the
            # library this path needs was finally installed.
            rebuilt.apply_transform(voxels.transform)
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


def _reach(body: trimesh.Trimesh, distance: float) -> NDArray[np.float64]:
    """How far each vertex must travel for its faces to move ``distance``.

    A vertex normal is the average of the normals of the faces meeting there,
    so on anything but a flat patch it points *between* them. Moved along it by
    ``distance``, each of those faces only advances by ``distance`` times the
    cosine between the two - and on the corner of a cube, where three faces
    meet at once, that cosine is 1/sqrt(3) and the faces move barely half as
    far as asked.

    Measured before this existed: a 1 mm slab asked to grow by 0.25 mm each
    side came back 1.29 mm rather than 1.5. Smooth organic geometry hides it,
    because there the vertex normal and the face normals nearly agree - which
    is exactly the kind of model that would have let this ship unnoticed.

    Dividing by the cosine restores it. The *smallest* cosine of the faces
    meeting at a vertex is the one that decides, so every face moves at least
    as far as asked. Clamped, because a sharp spine has a cosine near zero and
    would otherwise fling its tip across the model.
    """
    faces = np.asarray(body.faces)
    face_normals = np.asarray(body.face_normals, dtype=np.float64)
    vertex_normals = np.asarray(body.vertex_normals, dtype=np.float64)

    # The cosine between each face and each of its own three vertex normals.
    agreement = np.einsum("fij,fj->fi", vertex_normals[faces], face_normals)

    smallest = np.ones(len(vertex_normals), dtype=np.float64)
    np.minimum.at(smallest, faces.ravel(), agreement.ravel())
    return (distance / np.clip(smallest, _LEAST_AGREEMENT, 1.0))[:, None]


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
