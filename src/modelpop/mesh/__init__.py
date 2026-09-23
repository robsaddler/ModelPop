"""Mesh processing adapters (trimesh, manifold3d)."""

from modelpop.mesh.file_io import TrimeshIO
from modelpop.mesh.trimesh_ops import TrimeshOps, thinnest_wall

__all__ = ["TrimeshIO", "TrimeshOps", "thinnest_wall"]
