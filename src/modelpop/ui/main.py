"""Application entry point.

Composition happens here and nowhere else: this is the only module that knows
which concrete adapter satisfies which port. Everything below is wired through
protocols, which is what makes the stack swappable.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from modelpop.application.workspace import Workspace
from modelpop.domain.printer import PrinterProfile
from modelpop.mesh import TrimeshIO, TrimeshOps
from modelpop.printing import BambuSlicer
from modelpop.ui.main_window import MainWindow

__all__ = ["main"]


def build_workspace() -> Workspace:
    """Wire the application together.

    The slicer is optional: ModelPop is still useful for inspecting and
    repairing models on a machine with no slicer installed, so a missing one
    disables slicing rather than preventing start-up.
    """
    return Workspace(
        mesh_io=TrimeshIO(),
        mesh_ops=TrimeshOps(),
        slicer=BambuSlicer(),
        printer=PrinterProfile.p2s(),
    )


def main() -> int:
    """Start the application."""
    app = QApplication(sys.argv)
    app.setApplicationName("ModelPop")

    window = MainWindow(build_workspace())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
