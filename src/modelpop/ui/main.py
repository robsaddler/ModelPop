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
from modelpop.generation.gpu_lease import GpuLease
from modelpop.mesh import TrimeshDetailBake, TrimeshIO, TrimeshOps
from modelpop.paths import app_data_dir
from modelpop.printing import BambuLanGateway, BambuSlicer, ToolpathVerifier
from modelpop.projects import JsonProjectStore
from modelpop.repositories import (
    MYMINIFACTORY_KEY_NAME,
    THINGIVERSE_KEY_NAME,
    JsonAcceptanceStore,
    MyMiniFactoryRepository,
    ThingiverseRepository,
)
from modelpop.ui.branding import claim_the_taskbar, icon
from modelpop.ui.main_window import MainWindow
from modelpop.vision.photogrammetry import ColmapOpenMvsReconstructor

__all__ = ["build_discovery", "build_workspace", "main"]


def build_workspace(kernel: Build123dKernel | None = None) -> Workspace:
    """Wire the application together.

    Args:
        kernel: the CAD kernel to share. One is made if none is given, but
            sharing matters: each one costs its own two-and-a-half second
            probe, and two of them meant the application asked the same
            question twice on every start.

    Every external dependency is optional at start-up. A missing slicer
    disables slicing, a missing API key disables generation, and everything
    else still works. Refusing to start because one thing is absent would be
    a poor trade.
    """
    ops = TrimeshOps()
    mesh_io = TrimeshIO()
    # One lease, shared. Densifying a capture and generating from a picture
    # both want most of a 16 GB card, and two at once does not fail cleanly.
    card = GpuLease.beside(app_data_dir())
    return Workspace(
        mesh_io=mesh_io,
        mesh_ops=ops,
        slicer=BambuSlicer(),
        printer=PrinterProfile.p2s(),
        generator=CadLoopGenerator(AnthropicProvider(), kernel or Build123dKernel(), ops),
        gcode_verifier=ToolpathVerifier(),
        mesh_generator=TrellisCliGenerator(mesh_io, lease=card),
        # The real gateway is wired in, and sends nothing until the user
        # ticks the box in Settings. The window holds that switch; see
        # MainWindow._send_to_printer.
        printer_gateway=BambuLanGateway(),
        detail=TrimeshDetailBake(),
        reconstructor=ColmapOpenMvsReconstructor(mesh_io=mesh_io, lease=card),
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
    # Before the QApplication, so the taskbar has the identity from the start.
    claim_the_taskbar()

    app = QApplication(sys.argv)
    app.setApplicationName("ModelPop")
    app.setWindowIcon(icon())

    secrets = default_store()

    # Ask whether the CAD kernel is there *now*, on its own thread, so the
    # answer is ready by the time the window asks for it. Asked inline it costs
    # two and a half seconds of a closed window.
    # Started before the window is built, so the CAD panel's first question
    # finds a probe already running and is told "not yet" instead of waiting
    # two and a half seconds for it.
    kernel = Build123dKernel()
    kernel.start_probing()

    window = MainWindow(
        build_workspace(kernel),
        lambda: build_discovery(secrets),
        ModellingSession(
            Build123dCompiler(kernel),
            JsonProjectStore(),
            TrimeshIO(),
            TrimeshOps(),
        ),
    )
    # Started once the window exists, so the answer can be delivered to it.
    # Nothing waits for it: the CAD tools are greyed for a moment instead of
    # the window being held shut for the whole probe.
    window.kernel_is_being_probed_by(kernel)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
