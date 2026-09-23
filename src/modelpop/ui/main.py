"""Application entry point.

Composition happens here and nowhere else: this is the only module that knows
which concrete adapter satisfies which port. Everything below is wired through
protocols, which is what makes the stack swappable.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from modelpop.ai import AnthropicProvider
from modelpop.application.workspace import Workspace
from modelpop.cad import Build123dKernel
from modelpop.domain.printer import PrinterProfile
from modelpop.mesh import TrimeshIO, TrimeshOps
from modelpop.printing import BambuSlicer
from modelpop.ui.main_window import MainWindow

__all__ = ["main"]


def build_workspace() -> Workspace:
    """Wire the application together.

    Every external dependency is optional at start-up. A missing slicer
    disables slicing, a missing API key disables generation, and everything
    else still works. Refusing to start because one thing is absent would be
    a poor trade.
    """
    return Workspace(
        mesh_io=TrimeshIO(),
        mesh_ops=TrimeshOps(),
        slicer=BambuSlicer(),
        printer=PrinterProfile.p2s(),
        cad_kernel=Build123dKernel(),
        ai=AnthropicProvider(),
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
