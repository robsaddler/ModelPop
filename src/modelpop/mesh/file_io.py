"""Reading and writing mesh files.

Satisfies the ``MeshIO`` port. A malformed file is an expected outcome, not an
exception: users drag in whatever they downloaded, and half of it is broken.

Unit handling deserves care. STL and OBJ carry no units at all, so a file is
assumed to be millimetres - the convention every slicer uses. 3MF *does* declare
units, and we honour what it says.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from modelpop.domain.mesh import Mesh
from modelpop.domain.result import Result, failure, success
from modelpop.domain.units import Unit

__all__ = ["TrimeshIO"]

_SUFFIXES = frozenset({".stl", ".obj", ".ply", ".3mf", ".glb", ".gltf", ".off"})

# 3MF records units by name; everything else is assumed to be millimetres.
_THREEMF_UNITS = {
    "micron": Unit.MILLIMETRE,  # scaled below
    "millimeter": Unit.MILLIMETRE,
    "centimeter": Unit.CENTIMETRE,
    "meter": Unit.METRE,
    "inch": Unit.INCH,
    "foot": Unit.FOOT,
}


class TrimeshIO:
    """Mesh file IO via trimesh."""

    def supported_suffixes(self) -> frozenset[str]:
        """Suffixes this implementation can read."""
        return _SUFFIXES

    def load(self, path: Path) -> Result[Mesh]:
        """Read a mesh from disk.

        Scenes containing several objects are concatenated into one mesh, which
        is what a user dragging in a multi-part model expects to see.
        """
        if not path.exists():
            return failure("File not found", str(path))
        if path.suffix.lower() not in _SUFFIXES:
            return failure(
                "Unsupported file type",
                f"{path.suffix or 'no suffix'}; expected one of {', '.join(sorted(_SUFFIXES))}",
            )
        if path.stat().st_size == 0:
            return failure("The file is empty", str(path.name))

        try:
            loaded: Any = trimesh.load(path, force="mesh", process=False)
        except Exception as exc:
            return failure(f"Could not read {path.name}", f"{type(exc).__name__}: {exc}")

        if loaded is None:
            return failure(f"Could not read {path.name}", "the file contained no geometry")

        if isinstance(loaded, trimesh.Scene):
            if not loaded.geometry:
                return failure(f"Could not read {path.name}", "the scene was empty")
            loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))

        if not hasattr(loaded, "faces") or len(loaded.faces) == 0:
            return failure(
                f"Could not read {path.name}",
                "the file has no triangles; it may be a point cloud or a curve",
            )

        return success(
            Mesh(
                np.asarray(loaded.vertices, dtype=np.float64),
                np.asarray(loaded.faces, dtype=np.int32),
                self._unit_of(loaded),
            )
        )

    @staticmethod
    def _unit_of(loaded: Any) -> Unit:
        """Work out the file's unit, defaulting to millimetres.

        Millimetres is right for STL and OBJ by convention, and is what every
        slicer assumes.
        """
        units = getattr(loaded, "units", None)
        if isinstance(units, str):
            return _THREEMF_UNITS.get(units.lower(), Unit.MILLIMETRE)
        return Unit.MILLIMETRE

    def save(self, mesh: Mesh, path: Path) -> Result[Path]:
        """Write a mesh to disk, choosing the format from the suffix.

        The mesh is converted to millimetres first, because that is what every
        slicer expects and a file that lies about its scale is worse than useless.
        """
        if mesh.is_empty:
            return failure("Nothing to save", "the model has no geometry")
        if path.suffix.lower() not in _SUFFIXES:
            return failure("Unsupported file type", path.suffix or "no suffix")

        in_mm = mesh.to_unit(Unit.MILLIMETRE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            body = trimesh.Trimesh(
                vertices=np.asarray(in_mm.vertices, dtype=np.float64),
                faces=np.asarray(in_mm.faces, dtype=np.int64),
                process=False,
                validate=False,
            )
            body.export(path)
        except Exception as exc:
            return failure(f"Could not write {path.name}", f"{type(exc).__name__}: {exc}")

        if not path.exists() or path.stat().st_size == 0:
            return failure(f"Could not write {path.name}", "nothing was written")
        return success(path)
