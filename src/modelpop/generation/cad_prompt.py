"""What we tell the model when asking for a part.

The prompt is a module of its own because it is the part of Pipeline A most
likely to change, and because it should be reviewable as prose rather than
buried in a format string.

Two things shape it. Published CAD-generation work is consistent that giving a
model a small helper vocabulary improves success more than giving it a bigger
model. And every benchmark finds the same failure: the shape comes out roughly
right and the numbers come out wrong, so the prompt states the dimensions as a
contract and warns that the part will be measured against them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modelpop.application.cad_ports import DimensionTable
    from modelpop.domain.printer import PrinterProfile

__all__ = ["SYSTEM_PROMPT", "build_request"]


SYSTEM_PROMPT = """\
You write build123d scripts that produce single, printable, solid parts.

Return only Python code, in one block. No explanation, no commentary.

Rules:

1. Assign the finished part to a variable named `result`.
2. Build a solid, never a surface. Every part must be closed and watertight.
3. Work in millimetres. Never state a unit; the numbers are millimetres.
4. Produce one connected solid unless asked for an assembly. Every feature must
   touch the body.
5. Import only from build123d. No os, no sys, no file access, no network.

A worked example of the shape expected:

```python
from build123d import *

with BuildPart() as part:
    Box(60, 40, 8)
    with Locations((-20, 0, 0), (20, 0, 0)):
        Hole(radius=2.0)
    fillet(part.edges().filter_by(Axis.Z), radius=4)

result = part.part
```

Design for FDM printing:

- Walls below 0.8 mm will not survive; 1.5 mm or more is safer.
- Overhangs steeper than 45 degrees need support. Prefer chamfers to fillets on
  downward-facing edges, since a 45 degree chamfer prints unsupported.
- Holes print undersize. Add about 0.2 mm to the radius of a hole meant to fit
  a shaft or a screw.
- Sharp internal corners concentrate stress. A small fillet is usually worth it.

You will be told if the part does not measure what was asked for. When that
happens, fix that one problem and return the whole corrected script.\
"""


def build_request(
    request: str,
    table: DimensionTable | None = None,
    printer: PrinterProfile | None = None,
) -> str:
    """Compose the user turn: the request, the dimensions, and the constraints."""
    parts = [f"Make this part:\n\n{request}"]

    if table:
        lines = "\n".join(
            f"- {dimension.name}: {dimension.expected.format()} "
            f"(tolerance +/- {dimension.tolerance.format()})"
            for dimension in table.dimensions
        )
        parts.append(
            "It must measure exactly this. The finished solid will be measured "
            f"and rejected if it does not:\n\n{lines}"
        )

    if printer is not None:
        width, depth, height = printer.envelope
        parts.append(
            f"It must fit a build volume of {width.format(places=0)} x "
            f"{depth.format(places=0)} x {height.format(places=0)}, and print on "
            f"a {printer.nozzle.diameter.format()} nozzle."
        )

    parts.append("Return only the code.")
    return "\n\n".join(parts)
