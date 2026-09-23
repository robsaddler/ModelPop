"""Turning a model's colour into relief.

The failure every consumer AI-3D product shares, and the reason the plan's
thesis is that the generator is the easy part: a generative model paints its
fine detail into a texture, a slicer cannot see colour, and the print comes out
a smooth blob.

The port takes a **file**, not a mesh, and that is the whole shape of the
decision. The domain mesh is vertices and faces and carries no texture, which is
right - and after a bake there is nothing left to carry, because the detail is
geometry. So the texture enters at an adapter and never reaches the domain at
all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import Nozzle
from modelpop.domain.result import Result

__all__ = ["DetailRescue"]


@runtime_checkable
class DetailRescue(Protocol):
    """Baking a colour texture into the surface it describes."""

    def is_available(self) -> bool:
        """Whether this can run right now."""
        ...

    def describe(self) -> str:
        """What this does, for the settings panel."""
        ...

    def rescue(
        self,
        source: Path,
        depth_mm: float = 0.4,
        nozzle: Nozzle = Nozzle.STANDARD,
        layer_height_mm: float = 0.2,
    ) -> Result[Mesh]:
        """Read a textured model and return one whose detail is in its surface.

        The printer's numbers are arguments rather than assumptions: they decide
        the shallowest relief that will appear in a slice and the finest feature
        worth holding, and both change with the nozzle.

        Must not raise. A model with no texture on it is an ordinary outcome -
        everything drawn with the CAD tools is one - and belongs in the
        ``Result`` with a message saying so.
        """
        ...
