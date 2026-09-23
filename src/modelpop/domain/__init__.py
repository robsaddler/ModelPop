"""Pure domain model.

Imports no CAD kernel, no UI framework and no I/O. numpy is permitted because it
is the lingua franca of geometry in Python; see ``.importlinter``.
"""

from modelpop.domain.commands import (
    Command,
    CommandBus,
    Document,
    DocumentHistory,
    Feature,
    GenericCommand,
    Origin,
)
from modelpop.domain.mesh import BoundingBox, Mesh
from modelpop.domain.result import Failure, Result, Success, failure, success
from modelpop.domain.units import Length, Unit

__all__ = [
    "BoundingBox",
    "Command",
    "CommandBus",
    "Document",
    "DocumentHistory",
    "Failure",
    "Feature",
    "GenericCommand",
    "Length",
    "Mesh",
    "Origin",
    "Result",
    "Success",
    "Unit",
    "failure",
    "success",
]
