"""Baking a colour texture into the surface it describes.

The answer to the failure that every consumer AI-3D tool shares: the generator
puts its fine detail in a texture, the slicer cannot see colour, and the print
comes out a smooth blob. This pushes the texture into the geometry, where a
slicer can find it.

Measured rather than assumed (`docs/research/spike-detail-rescue.md`): the
displacement itself is exact and costs six milliseconds at ten thousand
vertices, it does not tear the mesh at the texture's seam, and the thing that
actually limits it is mesh density - which is why the mesh is subdivided first,
by an amount the domain works out from the detail asked for.

**Luminance is a guess at height, and this module does not pretend otherwise.**
A dark patch might be a groove or it might be a dark patch, and nothing here can
tell the difference. The depth is the user's to choose and the operation is
undoable, which is the right shape for something nobody can predict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from modelpop.domain.detail import plan_detail
from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import Nozzle
from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

__all__ = ["TrimeshDetailBake", "displace", "luminance_at"]

# Rec. 709, which weights green highest because eyes do. A flat average makes a
# red groove and a green ridge the same depth, which they visibly are not.
_LUMA = np.array([0.2126, 0.7152, 0.0722])


def luminance_at(texture: NDArray[np.floating], uv: NDArray[np.floating]) -> NDArray[np.float64]:
    """The brightness of a texture under each of a set of UV coordinates.

    Nearest-neighbour, and wrapped rather than clamped, because a texture
    repeats and a coordinate slightly past the edge belongs at the other side
    rather than smeared along it.
    """
    if texture.ndim == 3:
        flat = texture[:, :, :3].astype(np.float64) @ _LUMA
    else:
        flat = texture.astype(np.float64)
    if flat.max() > 1.0:
        flat = flat / 255.0

    height, width = flat.shape
    columns = np.clip((uv[:, 0] % 1.0) * width, 0, width - 1).astype(np.int64)
    # Image rows run downwards and texture coordinates run upwards.
    rows = np.clip((1.0 - (uv[:, 1] % 1.0)) * height, 0, height - 1).astype(np.int64)
    return np.asarray(flat[rows, columns], dtype=np.float64)


def displace(
    vertices: NDArray[np.floating],
    normals: NDArray[np.floating],
    height: NDArray[np.floating],
    amplitude_mm: float,
) -> NDArray[np.float64]:
    """Push each vertex along its own normal, in and out about where it was.

    The heights are **centred on their own mean** first. A bake that only ever
    pushes outward inflates the model, and the size the user asked for quietly
    stops being the size they get - which matters more here than anywhere,
    because the whole point of the scale work is that a measured model measures
    correctly.
    """
    shifted = np.asarray(height, dtype=np.float64)
    shifted = shifted - shifted.mean()
    peak = float(np.abs(shifted).max())
    if peak > 0:
        shifted = shifted / peak
    moved = (
        np.asarray(vertices, dtype=np.float64)
        + np.asarray(normals, dtype=np.float64) * (shifted * amplitude_mm)[:, None]
    )
    return np.asarray(moved, dtype=np.float64)


class TrimeshDetailBake:
    """Reads a textured model and returns one whose detail is in its surface.

    Takes a **file**, not a ``Mesh``. The domain mesh is vertices and faces and
    deliberately carries no texture or UVs; after a bake there is nothing to
    carry, because the detail is geometry. So the texture never enters the
    domain at all, which is the right place for that boundary.
    """

    def is_available(self) -> bool:
        """Whether this can run. It needs only what the app already installs."""
        return True

    def describe(self) -> str:
        """What this does, for the settings panel."""
        return "Bakes a model's colour texture into its surface"

    def rescue(
        self,
        source: Path,
        depth_mm: float = 0.4,
        nozzle: Nozzle = Nozzle.STANDARD,
        layer_height_mm: float = 0.2,
    ) -> Result[Mesh]:
        """Turn a textured model's colour into relief.

        Args:
            source: the generated file, which must carry a texture and UVs -
                a GLB from the generator does.
            depth_mm: how deep the relief should go.
            nozzle: what will print it, which sets the finest useful feature.
            layer_height_mm: which sets the shallowest visible one.
        """
        import trimesh

        try:
            loaded = trimesh.load(source, force="mesh", process=False)
        except Exception as unreadable:
            return failure(
                "That model could not be read",
                f"{type(unreadable).__name__}: {unreadable}",
            )

        if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
            return failure("That file has no geometry in it", source.name)

        found = _texture_and_uv(loaded)
        if found is None:
            return failure(
                "That model has no texture to bake",
                "Detail rescue takes the colour a generator painted on and turns "
                "it into relief. A model that was never textured has nothing to "
                "turn - which includes anything drawn with the CAD tools.",
            )
        texture, uv = found

        size = float(np.ptp(loaded.vertices, axis=0).max())
        plan = plan_detail(depth_mm, len(loaded.faces), size, nozzle, layer_height_mm)
        if not plan.is_worth_doing:
            return failure("The detail cannot be baked in", plan.refused)

        body, uv = _subdivided(loaded, uv, plan.subdivisions)
        height = luminance_at(texture, uv)
        moved = displace(body.vertices, body.vertex_normals, height, plan.amplitude_mm)

        return success(Mesh(moved, np.asarray(body.faces, dtype=np.int32)))


def _texture_and_uv(body: Any) -> tuple[NDArray[np.floating], NDArray[np.floating]] | None:
    """The base-colour image and the coordinates that index it, if both are there.

    Both halves are needed and either can be missing on its own - a model can
    carry UVs with no image, or a material with no coordinates - so this
    answers once rather than leaving two checks to drift apart.
    """
    visual = getattr(body, "visual", None)
    uv = getattr(visual, "uv", None)
    if uv is None or len(uv) != len(body.vertices):
        return None

    material = getattr(visual, "material", None)
    image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
    if image is None:
        return None

    try:
        texture = np.asarray(image, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if texture.ndim not in (2, 3) or texture.size == 0:
        return None

    return texture, np.asarray(uv, dtype=np.float64)


def _subdivided(
    body: Any, uv: NDArray[np.floating], times: int
) -> tuple[Any, NDArray[np.floating]]:
    """Quadruple the mesh, carrying the texture coordinates with it.

    The UVs have to be subdivided *alongside* the geometry or the new vertices
    index nothing and the bake reads garbage. ``subdivide`` takes the
    attributes it should interpolate, which is why they travel together here
    rather than being recomputed afterwards.
    """
    import trimesh

    for _ in range(times):
        vertices, faces, attributes = trimesh.remesh.subdivide(
            body.vertices, body.faces, vertex_attributes={"uv": uv}
        )
        body = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        uv = np.asarray(attributes["uv"], dtype=np.float64)
    return body, uv
