"""Application entry point.

Composition happens here and nowhere else: this is the only module that knows
which concrete adapter satisfies which port. Everything below is wired through
protocols, which is what makes the stack swappable.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from modelpop.ai import AnthropicProvider, default_store
from modelpop.ai.secrets import LayeredSecretStore
from modelpop.application.discovery_service import Discovery
from modelpop.application.modelling import ModellingSession
from modelpop.application.workspace import Workspace
from modelpop.cad import Build123dCompiler, Build123dKernel
from modelpop.domain.printer import PrinterProfile
from modelpop.generation import CadLoopGenerator, TrellisCliGenerator
from modelpop.mesh import TrimeshIO, TrimeshOps
from modelpop.printing import BambuSlicer, ToolpathVerifier
from modelpop.projects import JsonProjectStore
from modelpop.repositories import (
    MYMINIFACTORY_KEY_NAME,
    THINGIVERSE_KEY_NAME,
    JsonAcceptanceStore,
    MyMiniFactoryRepository,
    ThingiverseRepository,
)
from modelpop.ui.main_window import MainWindow

__all__ = ["build_discovery", "build_workspace", "main"]


def build_workspace() -> Workspace:
    """Wire the application together.

    Every external dependency is optional at start-up. A missing slicer
    disables slicing, a missing API key disables generation, and everything
    else still works. Refusing to start because one thing is absent would be
    a poor trade.
    """
    ops = TrimeshOps()
    mesh_io = TrimeshIO()
    return Workspace(
        mesh_io=mesh_io,
        mesh_ops=ops,
        slicer=BambuSlicer(),
        printer=PrinterProfile.p2s(),
        generator=CadLoopGenerator(AnthropicProvider(), Build123dKernel(), ops),
        gcode_verifier=ToolpathVerifier(),
        mesh_generator=TrellisCliGenerator(mesh_io),
    )


def build_discovery(secrets: LayeredSecretStore) -> Discovery:
    """Wire the model repositories.

    Every source is constructed whether or not it has a credential, so the
    gallery can say which ones are missing a key rather than pretending they do
    not exist. Which sources are here, and which are deliberately absent, is
    ADR-0008.

    Credentials are read here rather than inside the adapters: the composition
    root is the one place allowed to know both where secrets live and which
    concrete adapter needs them.
    """
    return Discovery(
        [
            MyMiniFactoryRepository(secrets.get(MYMINIFACTORY_KEY_NAME) or ""),
            ThingiverseRepository(secrets.get(THINGIVERSE_KEY_NAME) or ""),
        ],
        JsonAcceptanceStore(),
    )


def main() -> int:
    """Start the application."""
    app = QApplication(sys.argv)
    app.setApplicationName("ModelPop")

    secrets = default_store()
    window = MainWindow(
        build_workspace(),
        lambda: build_discovery(secrets),
        ModellingSession(Build123dCompiler(Build123dKernel()), JsonProjectStore()),
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
