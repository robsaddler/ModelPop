"""The main window.

Thin by design: it wires widgets to the view-model and does no work of its own.
Everything it calls is testable without a display.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor

from modelpop.ai import AnthropicProvider, default_store
from modelpop.application.ai_ports import AiSettings
from modelpop.application.modelling import ModellingSession
from modelpop.application.workspace import DEFAULT_TRIANGLE_BUDGET, Workspace, WorkspaceState
from modelpop.domain.printer import PrinterConnection
from modelpop.domain.readiness import Severity
from modelpop.generation import edit_by_description
from modelpop.presentation.modelling_view_model import ModellingViewModel, Outcome
from modelpop.presentation.workspace_view_model import Notification, WorkspaceViewModel
from modelpop.projects import EXTENSION as PROJECT_EXTENSION
from modelpop.rendering.viewport import ViewportScene

if TYPE_CHECKING:
    from collections.abc import Callable

    from modelpop.application.discovery_service import Discovery
    from modelpop.application.repository_ports import Download

from modelpop.presentation.measuring import MeasuringTool
from modelpop.presentation.sectioning import SectionTool
from modelpop.ui.cad_panel import CadPanel, ThreadedRebuilder
from modelpop.ui.dialogs import (
    EditDialog,
    GenerateDialog,
    GenerateFromImageDialog,
    ResizeDialog,
    RunLogDialog,
    SettingsDialog,
)
from modelpop.ui.section_dialog import SectionDialog

__all__ = ["MainWindow"]

# How far the mouse may travel between press and release and still count as
# a click rather than an orbit. A few pixels of wobble is a steady hand, not
# an attempt to rotate the model.
CLICK_SLOP_PIXELS = 4

# Readable on a dark panel. The default reds and greens are not: a blocker
# rendered in #C0392B on #2B3038 is almost invisible, which defeats the point
# of having a readiness panel at all.
_SEVERITY_COLOURS = {
    Severity.INFO: "#6FCF97",
    Severity.WARNING: "#F2C14E",
    Severity.BLOCKER: "#F2765A",
}


class MainWindow(QMainWindow):
    """Open a model, see it, and learn whether it will print."""

    def __init__(
        self,
        workspace: Workspace,
        discovery: Callable[[], Discovery] | None = None,
        modelling: ModellingSession | None = None,
    ) -> None:
        """Build the window around a workspace.

        Args:
            workspace: operations on the open mesh.
            discovery: builds a repository search, on demand, so a credential
                changed in Settings applies to the next search.
            modelling: the parametric CAD session. Optional, so the window
                still opens when the kernel failed to load.
        """
        super().__init__()
        # Work runs inline for now: the operations in Phase 1 are fast enough
        # that a worker thread would add risk without adding responsiveness.
        # The seam exists, so swapping in the threaded runner is a one-line change.
        self._view_model = WorkspaceViewModel(workspace)
        self._printer = workspace.printer
        self._secrets = default_store()
        # A factory rather than an instance: the credentials can change in
        # Settings while the window is open, and a source built at start-up
        # would keep the key the user just replaced.
        self._discovery = discovery
        self._ai_settings = AiSettings()
        # A print is the one irreversible thing here, so it stays off until
        # the user turns it on in Settings, every time the app starts.
        self._send_for_real = False

        # The two view-models own different things - a mesh and a feature tree -
        # and the tree hands its geometry to the workspace after every rebuild,
        # so the viewport, the readiness panel and the slicer all work on it
        # without knowing it came from the CAD tools.
        session = modelling or ModellingSession()
        self._modelling = ModellingViewModel(
            session,
            ThreadedRebuilder(self),
            self._view_model.adopt,
            lambda words: edit_by_description(
                session, words, AnthropicProvider(self._secrets), self._ai_settings
            ),
        )

        self.setWindowTitle("ModelPop")
        self.resize(1400, 900)

        self._viewport = QtInteractor(self)
        self._scene = ViewportScene(self._viewport, self._printer)
        self._measuring = MeasuringTool()
        self._section = SectionTool()
        self._section_panel: SectionDialog | None = None
        self._pressed_at: QPoint | None = None
        self._findings = QListWidget()
        self._summary = QLabel("Open a model to begin.")
        self._summary.setWordWrap(True)

        self._build_layout()
        self._build_menu()
        self._connect()
        self._recall_printer()

    def _recall_printer(self) -> None:
        """Take the printer's address back out of the credential store.

        Without this the user retypes three fields every launch. The switch
        that lets jobs actually leave the machine is *not* recalled: it starts
        off every time, because the cost of forgetting it is a print nobody
        asked for.
        """
        from modelpop.printing import ACCESS_CODE_NAME, HOST_NAME, SERIAL_NAME

        self._view_model.printer_connection = PrinterConnection(
            host=self._secrets.get(HOST_NAME) or "",
            serial=self._secrets.get(SERIAL_NAME) or "",
            access_code=self._secrets.get(ACCESS_CODE_NAME) or "",
        )

    # ------------------------------------------------------------------ build

    def _build_layout(self) -> None:
        side = QVBoxLayout()
        heading = QLabel("Print readiness")
        heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        side.addWidget(heading)
        side.addWidget(self._summary)

        # Findings are two lines each - the problem and what to do about it -
        # so they must wrap. Truncated advice is worse than none.
        self._findings.setWordWrap(True)
        self._findings.setSpacing(6)
        self._findings.setStyleSheet(
            "QListWidget { border: none; } QListWidget::item { padding: 6px 4px; }"
        )
        side.addWidget(self._findings, stretch=1)

        self._generate_button = QPushButton("Generate a part...")
        self._generate_button.setStyleSheet("font-weight: 600; padding: 8px;")
        side.addWidget(self._generate_button)

        self._edit_button = QPushButton("Change this part...")
        self._edit_button.setEnabled(False)
        side.addWidget(self._edit_button)

        self._how_button = QPushButton("How this part was made")
        self._how_button.setVisible(False)
        side.addWidget(self._how_button)

        self._repair_button = QPushButton("Repair")
        self._resize_button = QPushButton("Resize...")
        self._prepare_button = QPushButton("Place on bed")
        self._simplify_button = QPushButton(f"Simplify to {DEFAULT_TRIANGLE_BUDGET // 1000}k")
        self._slice_button = QPushButton("Slice")
        for button in (
            self._repair_button,
            self._resize_button,
            self._prepare_button,
            self._simplify_button,
            self._slice_button,
        ):
            button.setEnabled(False)
            side.addWidget(button)

        readiness = QWidget()
        readiness.setLayout(side)

        # Tabs rather than two stacked panels: the CAD tools and the readiness
        # report are used at different moments, and showing both at once leaves
        # no room for either.
        panel = QTabWidget()
        panel.addTab(readiness, "Print readiness")
        self._cad_panel = CadPanel(self._modelling)
        panel.addTab(self._cad_panel, "CAD tools")
        panel.setFixedWidth(420)

        layout = QHBoxLayout()
        layout.addWidget(self._viewport.interactor, stretch=1)
        layout.addWidget(panel)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._choose_file)
        file_menu.addAction(open_action)

        self._from_image_action = QAction("Make one from a &picture...", self)
        self._from_image_action.triggered.connect(self._from_image)
        file_menu.addAction(self._from_image_action)

        find_action = QAction("&Find a model to start from...", self)
        find_action.setShortcut("Ctrl+F")
        find_action.triggered.connect(self._find_a_model)
        file_menu.addAction(find_action)

        save_action = QAction("Export the &mesh as...", self)
        save_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_action.triggered.connect(self._choose_save_path)
        file_menu.addAction(save_action)
        file_menu.addSeparator()

        open_project = QAction("Open a &project...", self)
        open_project.triggered.connect(self._open_project)
        file_menu.addAction(open_project)

        self._save_project_action = QAction("Save the p&roject...", self)
        self._save_project_action.setEnabled(False)
        self._save_project_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_project_action.triggered.connect(self._save_project)
        file_menu.addAction(self._save_project_action)
        file_menu.addSeparator()

        settings_action = QAction("Se&ttings...", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        print_menu = self.menuBar().addMenu("&Print")
        slice_action = QAction("&Slice...", self)
        slice_action.triggered.connect(self._slice)
        print_menu.addAction(slice_action)

        self._watch_action = QAction("&Watch it print...", self)
        self._watch_action.setEnabled(False)
        self._watch_action.triggered.connect(self._watch_print)
        print_menu.addAction(self._watch_action)

        print_menu.addSeparator()
        self._send_action = QAction("Se&nd it to the printer...", self)
        self._send_action.setEnabled(False)
        self._send_action.triggered.connect(self._send_to_printer)
        print_menu.addAction(self._send_action)

        self._printer_status_action = QAction("What is the printer &doing?", self)
        self._printer_status_action.triggered.connect(self._view_model.read_printer_status)
        print_menu.addAction(self._printer_status_action)

        view_menu = self.menuBar().addMenu("&View")
        for label, name, shortcut in (
            ("&Isometric", "iso", "Ctrl+1"),
            ("&Top", "top", "Ctrl+2"),
            ("&Front", "front", "Ctrl+3"),
            ("&Right", "right", "Ctrl+4"),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _=False, n=name: self._scene.set_view(n))
            view_menu.addAction(action)

        frame_action = QAction("&Fit to model", self)
        frame_action.setShortcut(QKeySequence("Ctrl+0"))
        frame_action.triggered.connect(self._scene.frame_model)
        view_menu.addAction(frame_action)

        view_menu.addSeparator()
        self._measure_action = QAction("&Measure between two points", self)
        self._measure_action.setCheckable(True)
        self._measure_action.setShortcut(QKeySequence("Ctrl+M"))
        self._measure_action.toggled.connect(self._set_measuring)
        view_menu.addAction(self._measure_action)

        self._section_action = QAction("&Cut it open", self)
        self._section_action.setCheckable(True)
        self._section_action.setShortcut(QKeySequence("Ctrl+K"))
        self._section_action.toggled.connect(self._set_section)
        view_menu.addAction(self._section_action)

    # -------------------------------------------------------------- measuring

    def _set_measuring(self, on: bool) -> None:
        """Turn the measuring tool on or off.

        While it is on the viewport's own click handling is left alone - VTK
        still rotates the model on a drag - and a *click* is taken as a point.
        Stealing the drag as well would make the part unrotatable, which is the
        one thing somebody measuring it most needs to do.
        """
        if on:
            self._measuring.turn_on()
            self._viewport.interactor.installEventFilter(self)
        else:
            self._measuring.turn_off()
            self._viewport.interactor.removeEventFilter(self)
            self._scene.clear_measurement()
            self._viewport.render()
        self.statusBar().showMessage(self._measuring.describe())

    # --------------------------------------------------------------- section

    def _set_section(self, on: bool) -> None:
        """Cut the view open, or put it back together.

        The controls open beside the window rather than over it, because the
        point of a section is to drag it through the part and watch. Closing
        them switches the cut off, so there is no way to leave the view sliced
        with nothing on screen saying why.
        """
        if not on:
            self._section.turn_off()
            self._apply_section()
            if self._section_panel is not None:
                self._section_panel.hide()
            self.statusBar().showMessage(self._section.describe())
            return

        mesh = self._view_model.state.mesh
        self._section.fits(None if mesh is None else mesh.bounds)
        self._section.turn_on()

        if self._section_panel is None:
            self._section_panel = SectionDialog(self._section, self)
            self._section_panel.changed.connect(self._apply_section)
            # Unchecking the menu item is what switches the cut off, so the
            # panel closing has to go through it rather than round it.
            self._section_panel.finished.connect(lambda _: self._section_action.setChecked(False))
        self._section_panel.refresh()
        self._section_panel.show()
        self._apply_section()

    def _apply_section(self) -> None:
        """Push the current plane at the viewport and redraw."""
        self._scene.set_section(self._section.plane)
        self._viewport.render()
        if self._section.is_on:
            self.statusBar().showMessage(self._section.describe())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Take clicks in the viewport as measurement points.

        A *click*, not a drag: the mouse has to come back up within a few
        pixels of where it went down, or the user was orbiting the model and
        meant nothing by it. Never consumes the event, so VTK still gets it.
        """
        if not self._measuring.is_on or watched is not self._viewport.interactor:
            return super().eventFilter(watched, event)

        if isinstance(event, QMouseEvent):
            if event.type() == QEvent.Type.MouseButtonPress:
                self._pressed_at = event.position().toPoint()
            elif event.type() == QEvent.Type.MouseButtonRelease and self._pressed_at is not None:
                moved = (event.position().toPoint() - self._pressed_at).manhattanLength()
                self._pressed_at = None
                if moved <= CLICK_SLOP_PIXELS:
                    self._measure_at(event.position().x(), event.position().y())

        return super().eventFilter(watched, event)

    def _measure_at(self, x: float, y: float) -> None:
        """Turn one click into a measurement point, if it hit the model."""
        # Qt counts rows from the top of the widget, VTK from the bottom.
        flipped = self._viewport.interactor.height() - y
        hit = self._scene.pick_at(x, flipped)
        point = None if hit is None else (hit[0][0], hit[0][1], hit[0][2])

        if not self._measuring.picked(point) and point is None:
            self.statusBar().showMessage(
                "That click missed the model. " + self._measuring.describe()
            )
            return

        self._scene.show_measurement(self._measuring.points)
        self._viewport.render()
        self.statusBar().showMessage(self._measuring.describe())

    def _connect(self) -> None:
        self._modelling.on_outcome(self._on_cad_outcome)
        self._view_model.on_state_changed(self._on_state_changed)
        self._view_model.on_notification(self._on_notification)
        self._view_model.on_busy_changed(self._on_busy_changed)

        self._generate_button.clicked.connect(self._generate)
        self._edit_button.clicked.connect(self._edit_by_description)
        self._how_button.clicked.connect(self._show_run_log)
        self._repair_button.clicked.connect(self._view_model.repair)
        self._resize_button.clicked.connect(self._resize)
        self._prepare_button.clicked.connect(self._view_model.prepare_for_bed)
        self._simplify_button.clicked.connect(
            lambda: self._view_model.simplify(DEFAULT_TRIANGLE_BUDGET)
        )
        self._slice_button.clicked.connect(self._slice)

    # ---------------------------------------------------------------- actions

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a model", "", "3D models (*.stl *.obj *.3mf *.ply *.glb *.off)"
        )
        if path:
            self._view_model.open(Path(path))

    def _choose_save_path(self) -> None:
        if not self._view_model.state.has_model:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the model", "", "3D models (*.stl *.3mf *.obj *.ply)"
        )
        if path:
            self._view_model.save_as(Path(path))

    def _find_a_model(self) -> None:
        """Search the repositories for something to start from.

        The downloaded file lands beside wherever the user is working, or in
        their documents if nothing is open yet. Never in a library: ADR-0008
        explains why there is not one.
        """
        from modelpop.ui.gallery import GalleryDialog

        if self._discovery is None:
            QMessageBox.information(
                self,
                "ModelPop",
                "Searching for models is not wired up in this build.",
            )
            return

        into = self._download_directory()
        if into is None:
            return

        GalleryDialog(self._discovery(), into, self, self._opened_from_gallery).exec()

    def _download_directory(self) -> Path | None:
        """Where a model found in the gallery should land."""
        open_file = self._view_model.state.source_path
        if open_file is not None:
            return open_file.parent

        chosen = QFileDialog.getExistingDirectory(self, "Where should the model be saved?")
        return Path(chosen) if chosen else None

    def _opened_from_gallery(self, download: Download) -> None:
        """Open a model that arrived from a repository, keeping its credit."""
        self._view_model.open(download.path)
        self.statusBar().showMessage(f"From {download.attribution}", 15000)

    def _from_image(self) -> None:
        """Turn a photo or a drawing into a model.

        The picture is the whole input. What comes back is a mesh like any
        other, so everything downstream - repair, scaling, the readiness panel,
        slicing - already works on it.
        """
        if not self._view_model.can_generate_a_mesh:
            QMessageBox.information(
                self,
                "ModelPop",
                "Making a model from a picture is not set up.\n\n"
                + self._view_model.describe_mesh_generation()
                + "\n\nSee docs/10-mesh-generation.md.",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a picture", "", "Pictures (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return

        chosen = Path(path)
        dialog = GenerateFromImageDialog(chosen.name, self, image=chosen)
        if not dialog.exec():
            return

        self.statusBar().showMessage("Making a model from that picture...")
        self._view_model.generate_from_image(chosen, dialog.options(), self._on_generation_progress)

    def _on_generation_progress(self, fraction: float, message: str) -> None:
        """Show how a generation is getting on.

        It takes tens of seconds, and a window with no sign of life reads as a
        crash. Straight to the status bar because the message arrives on a
        worker thread and a dialog from there is undefined.
        """
        self.statusBar().showMessage(f"{message} ({fraction:.0%})")

    def _open_project(self) -> None:
        """Open a saved feature tree."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a project", "", f"ModelPop projects (*{PROJECT_EXTENSION})"
        )
        if path:
            self._modelling.open_from(Path(path))

    def _save_project(self) -> None:
        """Write the feature tree to a file.

        Separate from exporting the mesh, and worded so in the menu: a project
        is the steps and can still be changed, while an export is a shape and
        cannot.
        """
        if not self._modelling.can_save:
            QMessageBox.information(
                self, "ModelPop", "There is no model yet. Start one in the CAD tools."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the project", "", f"ModelPop projects (*{PROJECT_EXTENSION})"
        )
        if path:
            self._modelling.save_to(Path(path))

    def _resize(self) -> None:
        """Set the model's real size.

        The operation the user asked for by name - "about six inches tall" -
        and the one that makes a shape from a picture printable, since its
        scale is arbitrary until somebody says otherwise.
        """
        state = self._view_model.state
        if not state.has_model or state.mesh is None:
            return

        current = state.mesh.bounds.largest_dimension.format()
        dialog = ResizeDialog(current, self)
        if dialog.exec() and dialog.size_wanted is not None:
            self._view_model.scale_to_fit(dialog.size_wanted)

    def _watch_print(self) -> None:
        """Play back the last slice.

        Only offered once something has been sliced, because the toolpath is
        the only thing there is to watch - a mesh cannot say where the head
        goes or when.
        """
        from modelpop.ui.print_window import PrintWindow

        report = self._view_model.state.last_slice
        if report is None or report.gcode_path is None:
            QMessageBox.information(
                self, "ModelPop", "Slice the model first, then you can watch it print."
            )
            return

        PrintWindow(report.gcode_path, self._view_model.printer, self).exec()

    def _send_to_printer(self) -> None:
        """Send the last sliced job, after asking once whether to start it.

        The confirmation is not ceremony. Everything else in this application
        writes a file; this starts a machine in another room, on filament that
        costs money, and nothing in here can stop it afterwards. So the
        question is asked plainly, it names the printer, and the safe answer -
        put the file there and start it yourself - is the default button.
        """
        connection = self._view_model.printer_connection
        problem = connection.problem
        if problem is not None:
            QMessageBox.information(self, "ModelPop", f"{problem}\n\nAdd it in File > Settings.")
            return

        if not self._send_for_real:
            # Dry run: described, not sent. No dialog, because nothing is
            # about to happen that would need confirming.
            self._view_model.send_to_printer()
            return

        ask = QMessageBox(self)
        ask.setWindowTitle("Send it to the printer")
        ask.setText(f"Send this job to {connection.describe()}?")
        ask.setInformativeText(
            "Starting it now begins a print you cannot stop from here - only at the printer itself."
        )
        upload = ask.addButton("Send the file only", QMessageBox.ButtonRole.AcceptRole)
        start = ask.addButton("Send it and start printing", QMessageBox.ButtonRole.DestructiveRole)
        ask.addButton(QMessageBox.StandardButton.Cancel)
        ask.setDefaultButton(upload)
        ask.exec()

        chosen = ask.clickedButton()
        if chosen is upload:
            self._view_model.send_to_printer(for_real=True)
        elif chosen is start:
            self._view_model.send_to_printer(start_now=True, for_real=True)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self._secrets,
            self._ai_settings,
            self,
            self._view_model.describe_mesh_generation(),
            self._scene.describe_renderer(),
        )
        if dialog.exec():
            self._ai_settings = dialog.settings()
            self._view_model.ai_settings = self._ai_settings
            self._view_model.printer_connection = dialog.printer_connection
            self._send_for_real = dialog.send_for_real
            self.statusBar().showMessage("Settings saved.", 5000)
            self._refresh_buttons()

    def _generate(self) -> None:
        if not self._view_model.can_generate:
            QMessageBox.information(
                self,
                "ModelPop",
                "Generating a part needs an Anthropic API key.\n\nAdd one in File > Settings.",
            )
            return
        dialog = GenerateDialog(self)
        if dialog.exec():
            self._view_model.generate_part(dialog.request())

    def _edit_by_description(self) -> None:
        dialog = EditDialog(self)
        if dialog.exec():
            self._view_model.edit_part(dialog.instruction())

    def _show_run_log(self) -> None:
        run = self._view_model.state.last_generation
        if run is None or run.best is None:
            return
        RunLogDialog(run.log(), run.best.script, self).show()

    def _slice(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Where should the G-code go?")
        if directory:
            self._view_model.slice(Path(directory))

    # --------------------------------------------------------------- updating

    def _on_state_changed(self, state: WorkspaceState) -> None:
        report = state.readiness
        has_problems = report is not None and not report.is_printable
        self._scene.show_mesh(state.mesh, has_problems=has_problems)
        self._scene.frame_model()

        # A new model is a new range for the cut, and a new actor with no
        # clipping on it. Both have to be reapplied or the section silently
        # stops working on everything opened after the first one.
        self._section.fits(None if state.mesh is None else state.mesh.bounds)
        if self._section_panel is not None:
            self._section_panel.refresh()
        self._scene.set_section(self._section.plane)

        self.setWindowTitle(f"ModelPop - {state.title}")
        self.statusBar().showMessage(state.describe())

        self._findings.clear()
        if report is None:
            self._summary.setText("Open a model to begin.")
        else:
            self._summary.setText(report.summary())
            self._summary.setStyleSheet(
                f"color: {_SEVERITY_COLOURS[report.verdict]}; font-weight: bold;"
            )
            for finding in report.findings:
                item = QListWidgetItem(f"{finding.message}\n{finding.remedy}")
                item.setForeground(QColor(_SEVERITY_COLOURS[finding.severity]))
                item.setToolTip(f"{finding.rule}: {finding.message}")
                self._findings.addItem(item)

        self._how_button.setVisible(state.last_generation is not None)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        """Enable only what the current state actually allows."""
        state = self._view_model.state
        self._repair_button.setEnabled(self._view_model.can_repair)
        self._resize_button.setEnabled(state.has_model)
        self._prepare_button.setEnabled(state.has_model)
        self._simplify_button.setEnabled(state.has_model)
        self._slice_button.setEnabled(self._view_model.can_slice)
        self._generate_button.setEnabled(not self._view_model.is_busy)
        self._edit_button.setEnabled(self._view_model.can_edit_by_description)
        self._save_project_action.setEnabled(self._modelling.can_save)
        sliced = state.last_slice
        self._watch_action.setEnabled(sliced is not None and sliced.gcode_path is not None)
        self._send_action.setEnabled(self._view_model.can_send_to_printer)

    def _on_cad_outcome(self, outcome: Outcome) -> None:
        """Report what a CAD command did.

        A refused command is shown in the status bar rather than a dialog: the
        model is untouched and still correct, so interrupting the user with a
        modal would overstate it.
        """
        message = f"{outcome.message}. {outcome.detail}" if outcome.detail else outcome.message
        self.statusBar().showMessage(message, 10000)
        # The CAD tools can create a model without the workspace changing, so
        # the menu has to be refreshed from here too.
        self._save_project_action.setEnabled(self._modelling.can_save)

    def _on_notification(self, notification: Notification) -> None:
        self.statusBar().showMessage(notification.message, 8000)
        if notification.is_error:
            QMessageBox.warning(
                self, "ModelPop", f"{notification.message}\n\n{notification.detail}"
            )

    def _on_busy_changed(self, busy: bool) -> None:
        self.setCursor(Qt.CursorShape.WaitCursor if busy else Qt.CursorShape.ArrowCursor)
