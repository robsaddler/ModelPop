"""The main window.

Thin by design: it wires widgets to the view-model and does no work of its own.
Everything it calls is testable without a display.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
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

from modelpop.domain.placement import settle_onto_bed
from modelpop.presentation.dragging import movement_in
from modelpop.presentation.measuring import MeasuringTool
from modelpop.presentation.sectioning import SectionTool
from modelpop.ui.background import BackgroundRunner
from modelpop.ui.branding import icon
from modelpop.ui.cad_panel import CadPanel
from modelpop.ui.dialogs import (
    EditDialog,
    GenerateDialog,
    GenerateFromImageDialog,
    ResizeDialog,
    RunLogDialog,
    SettingsDialog,
)
from modelpop.ui.earlier_dialog import EarlierModelsDialog
from modelpop.ui.monitor_dialog import MonitorDialog
from modelpop.ui.place_dialog import PlaceDialog
from modelpop.ui.reconstruct_dialog import ReconstructDialog
from modelpop.ui.resize_dialog import ResizeObjectDialog
from modelpop.ui.section_dialog import SectionDialog
from modelpop.ui.variants_panel import VariantsPanel

__all__ = ["MainWindow"]

# How far the mouse may travel between press and release and still count as
# a click rather than an orbit. A few pixels of wobble is a steady hand, not
# an attempt to rotate the model.
CLICK_SLOP_PIXELS = 4

# Above this, a rebuild's duration is put in the status bar. Below it the
# number is noise; above it, it is the thing the user wants to know.
SAY_HOW_LONG_ABOVE_SECONDS = 1.0

# Readable on a dark panel. The default reds and greens are not: a blocker
# rendered in #C0392B on #2B3038 is almost invisible, which defeats the point
# of having a readiness panel at all.
_SEVERITY_COLOURS = {
    Severity.INFO: "#6FCF97",
    Severity.WARNING: "#F2C14E",
    Severity.BLOCKER: "#F2765A",
}


class _WindowSignals(QObject):
    """Carries the view-models' announcements back to the interface thread.

    The same thing ``CadPanel`` does, and for a reason this window learned the
    hard way rather than by symmetry.

    A CAD rebuild runs on a worker thread. It finishes by handing its mesh to
    the workspace view-model, which announces to *this* window - so every
    listener below was running on that worker. They touch Qt widgets and, worse,
    the VTK render window, whose OpenGL context belongs to the interface thread
    and nowhere else.

    The result was not a clean error. Geometry came back drawn wrong, and the
    next orbit deadlocked the whole application with every thread waiting on
    something another thread held. Measured on a hung process: fifty-seven
    threads, all in Wait, seven seconds of processor time between them.
    """

    state_changed = Signal(object)
    notified = Signal(object)
    cad_outcome = Signal(object)
    model_changed = Signal(object)
    kernel_answered = Signal()
    busy_changed = Signal(bool)


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
        # One runner, and *everything* slow goes through it. Slicing, generating
        # a mesh, reconstructing from photographs and sending a job are all
        # subprocesses or sockets taking seconds to minutes; every one of them
        # used to run here, on the interface thread, because the seam was left
        # inline in Phase 1 when nothing took long and the comment saying so was
        # never revisited.
        self._work = BackgroundRunner(self)
        self._view_model = WorkspaceViewModel(workspace, self._work)
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
            self._work,
            self._adopt_from_the_scene,
            lambda words: edit_by_description(
                session, words, AnthropicProvider(self._secrets), self._ai_settings
            ),
        )

        self.setWindowTitle("ModelPop")
        self.setWindowIcon(icon())
        self.resize(1400, 900)

        # Built before anything is connected: every announcement from a
        # worker thread crosses back through these.
        self._signals = _WindowSignals()

        self._viewport = QtInteractor(self)
        self._scene = ViewportScene(self._viewport, self._printer)
        self._measuring = MeasuringTool()
        self._section = SectionTool()
        self._section_panel: SectionDialog | None = None
        self._place_panel: PlaceDialog | None = None
        self._resize_panel: ResizeObjectDialog | None = None
        self._pressed_at: QPoint | None = None
        # True between letting go of a handle and the rebuild landing.
        self._drag_in_flight = False
        # The scene owns the geometry; the workspace holds a copy of it for
        # readiness, slicing and export. This is the hash of the copy the scene
        # last handed over, so a mesh appearing in the workspace that the scene
        # did not produce is recognisable as something arriving from outside.
        self._scene_hash = ""
        # True while a *new* model is on its way in - opened, generated,
        # reconstructed - as opposed to the one that is there being reworked.
        self._bringing_in_a_new_model = False
        # True between turning an object and putting it back on the plate.
        self._reseat_when_it_settles = False
        # True while the lock checkboxes are being made exclusive, so their
        # own toggles do not run this again.
        self._settling_locks = False
        self._findings = QListWidget()
        self._summary = QLabel("Open a model to begin.")
        self._summary.setWordWrap(True)

        self._build_layout()
        self._build_menu()
        self._build_scene_menu()
        self._connect()
        # Clicks in the viewport select objects, so this is on from the start
        # rather than only while the measuring tool is.
        self._viewport.interactor.installEventFilter(self)
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

        # Only ever shown for a model that has a texture, which is a generated
        # one. Offering it on a box drawn in the CAD tools would put a button
        # in front of the user that can only fail.
        self._detail_button = QPushButton("Rescue the detail...")
        self._detail_button.setToolTip(
            "Bake the model's colour into its surface, so the detail survives "
            "being sliced instead of printing as a smooth blob"
        )
        self._detail_button.setVisible(False)
        self._detail_button.clicked.connect(self._rescue_detail)
        side.addWidget(self._detail_button)

        # Hidden until something has been generated. An empty "shapes made this
        # session" list is clutter on the panel people use most.
        self._variants = VariantsPanel()
        self._variants.setVisible(False)
        self._variants.chosen.connect(self._show_a_variant)
        side.addWidget(self._variants)

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
        self._cad_panel.place_requested.connect(self._open_place_panel)
        panel.addTab(self._cad_panel, "CAD tools")
        panel.setFixedWidth(420)

        # The viewport and the handful of controls that belong *to* it, rather
        # than to the model. On screen rather than in a menu because they are
        # switched while looking at the thing they change.
        viewport_side = QVBoxLayout()
        viewport_side.setContentsMargins(0, 0, 0, 0)
        viewport_side.addWidget(self._viewport.interactor, stretch=1)
        viewport_side.addLayout(self._view_controls())

        held = QWidget()
        held.setLayout(viewport_side)

        layout = QHBoxLayout()
        layout.addWidget(held, stretch=1)
        layout.addWidget(panel)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

    def _view_controls(self) -> QHBoxLayout:
        """The row under the viewport: what to show, and how it may be turned."""
        row = QHBoxLayout()

        self._axes_box = QCheckBox("Show X, Y, Z")
        self._axes_box.setChecked(True)
        self._axes_box.setToolTip("Mark the axes on the front-left corner of the plate.")
        self._axes_box.toggled.connect(self._set_axes)
        row.addWidget(self._axes_box)

        row.addSpacing(18)
        row.addWidget(QLabel("Lock the view:"))

        # Checkboxes rather than a dial, because the useful gesture is to tick
        # one, work, and untick it. They behave exclusively: turning a part by
        # dragging the view is how it ends up skewed, and being locked to two
        # planes at once means nothing.
        self._lock_boxes: dict[str, QCheckBox] = {}
        for plane, label, tip in (
            ("front", "Front (X-Z)", "Looking along Y. Left and right stay left and right."),
            ("side", "Side (Y-Z)", "Looking along X."),
            ("top", "Top (X-Y)", "Looking down Z, at the plate."),
        ):
            box = QCheckBox(label)
            box.setToolTip(f"{tip} Pans and zooms, never turns.")
            box.toggled.connect(lambda on, p=plane: self._set_lock(p, on))
            self._lock_boxes[plane] = box
            row.addWidget(box)

        row.addStretch(1)
        return row

    def _set_axes(self, on: bool) -> None:
        """Show or hide the axis markers on the plate."""
        self._scene.show_axes(on)
        self._viewport.render()

    def _set_lock(self, plane: str, on: bool) -> None:
        """Hold the view on one plane, or let it turn freely again.

        Exclusive by hand rather than by a button group, because a group that
        enforces exclusivity will not let the last one be unticked - and
        unticking is how you get back to turning the model about freely.
        """
        if self._settling_locks:
            return

        self._settling_locks = True
        try:
            for name, box in self._lock_boxes.items():
                if name != plane:
                    box.setChecked(False)
        finally:
            self._settling_locks = False

        self._scene.lock_to(plane if on else None)
        self._viewport.render()
        self.statusBar().showMessage(
            f"View locked to {plane}. It pans and zooms but will not turn."
            if on
            else "View unlocked.",
            6000,
        )

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._choose_file)
        file_menu.addAction(open_action)

        self._from_image_action = QAction("Make one from a &picture...", self)
        self._from_image_action.triggered.connect(self._from_image)
        file_menu.addAction(self._from_image_action)

        self._from_photos_action = QAction("Measure one from se&veral photographs...", self)
        self._from_photos_action.triggered.connect(self._from_photos)
        file_menu.addAction(self._from_photos_action)

        earlier_action = QAction("Open one made &earlier...", self)
        earlier_action.setToolTip(
            "Models this application made before and never saved are still on "
            "the disk. Open one rather than generating it again."
        )
        earlier_action.triggered.connect(self._open_an_earlier_model)
        file_menu.addAction(earlier_action)

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
        self._printer_status_action.triggered.connect(self._watch_the_printer)
        print_menu.addAction(self._printer_status_action)

        view_menu = self.menuBar().addMenu("&View")

        # First, because it is the one people reach for when they have got
        # lost: square on to the printer with the whole build volume in frame.
        home_action = QAction("Look into the &printer", self)
        home_action.setShortcut(QKeySequence("Home"))
        home_action.setToolTip("Square on to the printer, whole build volume in frame")
        home_action.triggered.connect(self._look_into_the_printer)
        view_menu.addAction(home_action)
        view_menu.addSeparator()

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

        self._place_action = QAction("&Move and turn it...", self)
        self._place_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self._place_action.triggered.connect(self._open_place_panel)
        view_menu.addAction(self._place_action)

        self._drag_action = QAction("&Drag it about", self)
        self._drag_action.setCheckable(True)
        self._drag_action.setShortcut(QKeySequence("Ctrl+D"))
        self._drag_action.toggled.connect(self._set_dragging)
        view_menu.addAction(self._drag_action)

    # -------------------------------------------------------------- measuring

    def _set_measuring(self, on: bool) -> None:
        """Turn the measuring tool on or off.

        While it is on the viewport's own click handling is left alone - VTK
        still rotates the model on a drag - and a *click* is taken as a point.
        Stealing the drag as well would make the part unrotatable, which is the
        one thing somebody measuring it most needs to do.
        """
        # The filter stays installed either way: clicks in the viewport also
        # pick objects up, which has to work whether or not anything is being
        # measured.
        if on:
            self._measuring.turn_on()
        else:
            self._measuring.turn_off()
            self._scene.clear_measurement()
            self._viewport.render()
        self.statusBar().showMessage(self._measuring.describe())

    # -------------------------------------------------------------- dragging

    def _open_place_panel(self) -> None:
        """Open the panel that moves the part with buttons rather than a gizmo.

        Kept once opened, so the step size and axis the user chose survive
        closing it - placing a part is a lot of small repeated adjustments.
        """
        if self._modelling.state.is_empty:
            QMessageBox.information(
                self,
                "ModelPop",
                "Moving works on a part with a feature tree.\n\n"
                "Start one in the CAD tools tab, or describe what you want.",
            )
            return

        if self._place_panel is None:
            self._place_panel = PlaceDialog(self._modelling, self)
        self._place_panel.show()
        self._place_panel.raise_()
        self._place_panel.activateWindow()

    def _set_dragging(self, on: bool) -> None:
        """Put drag handles on the model, or take them off.

        Only for a part with a feature tree. A drag ends as ``Move`` and
        ``Rotate`` commands, and an imported mesh has nowhere to put them -
        so rather than dragging something that springs back on the next
        rebuild, it says why.
        """
        if not on:
            self._scene.stop_dragging()
            self._viewport.render()
            self.statusBar().showMessage("The drag handles are off.")
            return

        if self._modelling.state.is_empty:
            self._drag_action.setChecked(False)
            QMessageBox.information(
                self,
                "ModelPop",
                "Drag handles work on a part with a feature tree.\n\n"
                "Start one in the CAD tools tab, or describe what you want.",
            )
            return

        if not self._scene.start_dragging(self._dragged, self._dragging):
            self._drag_action.setChecked(False)
            self.statusBar().showMessage("There is nothing on the plate to drag.")
            return

        self._viewport.render()
        self.statusBar().showMessage(
            "Drag an arrow to move the part, or a ring to turn it. "
            "Each drag joins the feature tree and undoes."
        )

    def _dragging(self, matrix: object) -> None:
        """Say how far the part has moved, while it is still moving.

        Qt is not touched from a worker here - the widget's callbacks run on
        the interface thread, because that is where the mouse events arrive.
        """
        drag = movement_in(matrix)  # type: ignore[arg-type]
        self.statusBar().showMessage(drag.describe())

    def _dragged(self, matrix: object) -> None:
        """Turn a released drag into commands on the bus.

        The part is left where it was dragged to until the rebuild arrives.
        That rebuild is a subprocess and takes seconds, and snapping the part
        back to where it started for the whole of that was reported - fairly -
        as the drag being thrown away: you let go, and it jumps back.

        The handles stay on screen for the same interval but stop accepting a
        grab. A second drag started against a part whose move is still in
        flight is refused by the command bus and silently lost - so it must
        not be possible to start one, but taking the handles away to achieve
        that made them disappear and come back, which is worse. They go quiet
        and dim instead, and wake up over the new geometry in
        ``_on_state_changed``.

        Nothing here undoes the transform, because the actor is replaced
        wholesale by the rebuild and ``show_mesh`` clears the new one.
        """
        drag = movement_in(matrix)  # type: ignore[arg-type]

        if not drag.did_anything:
            # Nothing was recorded, so nothing will come back to replace it.
            self._scene.forget_drag()
            self._viewport.render()
            self.statusBar().showMessage(drag.describe())
            return

        self._drag_in_flight = True
        self._scene.pause_dragging()
        if drag.is_a_resize:
            # A proportion rather than a command: only the session knows how
            # big the part is now, and the feature has to record an absolute
            # size or it would compound every time anything earlier in the
            # tree changed.
            self._modelling.scale_selected_by(drag.resize)
        else:
            for command in drag.commands:
                self._modelling.apply_from_the_viewport(command)
        self.statusBar().showMessage(drag.describe())

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
        """Turn clicks in the viewport into selections, or measurement points.

        A *click*, not a drag: the mouse has to come back up within a few
        pixels of where it went down, or the user was orbiting the scene and
        meant nothing by it. Never consumes the event, so VTK still gets it and
        orbiting keeps working.

        Left picks up an object. Right picks it up and asks what to do with it,
        which is how anybody who has used a 3D tool expects to work - reaching
        for a menu item to switch on a gizmo is not.
        """
        if watched is not self._viewport.interactor or not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.MouseButtonPress:
            self._pressed_at = event.position().toPoint()
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.MouseButtonRelease and self._pressed_at is not None:
            moved = (event.position().toPoint() - self._pressed_at).manhattanLength()
            self._pressed_at = None
            if moved > CLICK_SLOP_PIXELS:
                return super().eventFilter(watched, event)

            x, y = event.position().x(), event.position().y()
            if self._measuring.is_on:
                self._measure_at(x, y)
            elif event.button() == Qt.MouseButton.RightButton:
                self._offer_the_scene_menu(x, y, event.globalPosition().toPoint())
            elif event.button() == Qt.MouseButton.LeftButton:
                self._select_at(x, y)

        return super().eventFilter(watched, event)

    def _build_scene_menu(self) -> None:
        """The menu a right-click in the viewport opens.

        Everything here acts on the object that was clicked. Reaching for a
        menu bar to switch on a gizmo is not how anybody works in a 3D tool -
        you point at the thing and say what you want.
        """
        self._scene_menu = QMenu(self)
        self._scene_menu.setToolTipsVisible(True)

        self._move_here_action = self._scene_menu.addAction("&Move and turn it...")
        self._move_here_action.triggered.connect(self._open_place_panel)

        self._handles_action = self._scene_menu.addAction("Put &handles on it")
        self._handles_action.triggered.connect(lambda: self._drag_action.setChecked(True))

        # Quarter turns, right there. Standing a model up that came out on its
        # back is the commonest single thing anybody does to a model from a
        # picture, and it should not need a panel opened to do it.
        turn = self._scene_menu.addMenu("&Turn it")
        self._turn_actions = []
        for label, degrees, axis in (
            ("Stand it &up (90 about X)", 90.0, "X"),
            ("Lay it &back (-90 about X)", -90.0, "X"),
            ("Tip it &right (90 about Y)", 90.0, "Y"),
            ("Tip it &left (-90 about Y)", -90.0, "Y"),
            ("Spin it a &quarter (90 about Z)", 90.0, "Z"),
            ("&Half turn (180 about Z)", 180.0, "Z"),
        ):
            action = turn.addAction(label)
            action.triggered.connect(lambda _=False, d=degrees, a=axis: self._turn_it(d, a))
            self._turn_actions.append(action)
        self._turn_menu = turn

        self._resize_here_action = self._scene_menu.addAction("&Resize it...")
        self._resize_here_action.triggered.connect(self._resize_whatever_is_there)

        self._drop_action = self._scene_menu.addAction("&Drop it on the bed")
        self._drop_action.triggered.connect(self._drop_whatever_is_there)

        self._scene_menu.addSeparator()

        self._repair_here_action = self._scene_menu.addAction("Re&pair it")
        self._repair_here_action.triggered.connect(self._view_model.repair)

        self._simplify_here_action = self._scene_menu.addAction("&Simplify it")
        self._simplify_here_action.triggered.connect(
            lambda: self._view_model.simplify(DEFAULT_TRIANGLE_BUDGET)
        )

        self._scene_menu.addSeparator()

        self._duplicate_action = self._scene_menu.addAction("Du&plicate it")
        self._duplicate_action.triggered.connect(self._modelling.duplicate_selected)

        self._rename_action = self._scene_menu.addAction("Re&name it...")
        self._rename_action.triggered.connect(self._rename_selected)

        self._delete_action = self._scene_menu.addAction("De&lete it")
        self._delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self._delete_action.triggered.connect(self._delete_selected)
        self.addAction(self._delete_action)

    def _refresh_scene_menu(self) -> None:
        """Offer only what there is something to do it to.

        There are two kinds of thing on the plate and they can do different
        things. A part with a feature tree can be moved, turned, copied and
        deleted, because every one of those is a step the tree can record. A
        mesh that arrived whole - opened from a file, made from a photograph,
        reconstructed - has no tree to put steps in; it can still be dropped on
        the bed, resized, repaired and simplified, because those rewrite the
        mesh itself.

        The menu used to ask only the first question, so a model made from a
        photograph came up with every item greyed and no way to do anything
        with it at all.
        """
        if not hasattr(self, "_scene_menu"):
            return

        a_part = self._modelling.selected_body is not None
        a_mesh = not a_part and self._view_model.state.has_model

        # Said, not merely greyed. An item that is off for a reason nobody can
        # see is the same complaint as an empty panel.
        why = (
            "This model arrived whole, so there is no feature tree to record "
            "the step in. Start a shape in the CAD tools to build something "
            "that can be moved, copied and undone step by step."
            if a_mesh
            else "Select an object first."
        )
        for action in (
            self._move_here_action,
            self._handles_action,
            self._duplicate_action,
            self._rename_action,
            self._delete_action,
        ):
            action.setEnabled(a_part)
            action.setToolTip("" if a_part else why)

        for action in (self._resize_here_action, self._drop_action):
            action.setEnabled(a_part or a_mesh)
        self._turn_menu.setEnabled(a_part)
        for action in self._turn_actions:
            action.setEnabled(a_part)

        self._repair_here_action.setEnabled(a_mesh and self._view_model.can_repair)
        self._simplify_here_action.setEnabled(a_mesh)

    def _open_resize_panel(self) -> None:
        """Open the panel that scales the selected object.

        Kept once opened, like the move panel: resizing is a few goes at it
        with a look in between, not one number typed once.
        """
        if self._modelling.selected_body is None:
            self.statusBar().showMessage("Select something to resize first.", 5000)
            return
        if self._resize_panel is None:
            self._resize_panel = ResizeObjectDialog(self._modelling, self)
        self._resize_panel.show()
        self._resize_panel.raise_()
        self._resize_panel.activateWindow()

    def _turn_it(self, degrees: float, axis: str) -> None:
        """Turn the selected object a quarter or a half, and reseat it.

        A turn is about the world origin - that is what the feature records -
        so a part that was standing on the plate is under it afterwards. Every
        slicer reseats a model after turning it for exactly this reason, and
        having to notice and fix it by hand each time is not a feature.
        """
        if self._modelling.selected_body is None:
            return
        self._modelling.rotate(degrees, axis)
        self._reseat_when_it_settles = True

    def _reseat_if_asked(self) -> None:
        """Put a just-turned object back on the bed."""
        if not self._reseat_when_it_settles or self._modelling.is_busy:
            return
        self._reseat_when_it_settles = False
        body = self._modelling.selected_body
        if body is None:
            return
        move = settle_onto_bed(body.bounds)
        if move.dz:
            self._modelling.move(move.dx, move.dy, move.dz)

    def _drop_whatever_is_there(self) -> None:
        """Settle whatever is on the plate onto it, part or mesh."""
        if self._modelling.selected_body is not None:
            self._drop_selected()
        elif self._view_model.state.has_model:
            self._view_model.prepare_for_bed()

    def _resize_whatever_is_there(self) -> None:
        """Resize a part through its own panel, a mesh through the older one."""
        if self._modelling.selected_body is not None:
            self._open_resize_panel()
        elif self._view_model.state.has_model:
            self._resize()

    def _drop_selected(self) -> None:
        """Settle the selected object onto the plate."""
        body = self._modelling.selected_body
        if body is None:
            return
        move = settle_onto_bed(body.bounds)
        if not move.dz:
            self.statusBar().showMessage("It is already on the bed.", 5000)
            return
        self._modelling.move(move.dx, move.dy, move.dz)

    def _rename_selected(self) -> None:
        body = self._modelling.selected_body
        if body is None:
            return
        name, said_yes = QInputDialog.getText(self, "ModelPop", "Call it:", text=body.label)
        if said_yes and name.strip():
            self._modelling.rename_selected(name)

    def _delete_selected(self) -> None:
        body = self._modelling.selected_body
        if body is None:
            return
        self._modelling.delete_selected()

    # ----------------------------------------------------------- the objects

    def _body_at(self, x: float, y: float) -> str | None:
        """Which object is under a point in the viewport."""
        # Qt counts rows from the top of the widget, VTK from the bottom.
        height = self._viewport.interactor.height()
        ratio = self._device_ratio()
        return self._scene.body_at(x * ratio, (height - y) * ratio)

    def _device_ratio(self) -> float:
        """How many device pixels the render window puts in one Qt pixel.

        This display scales at 150%, and VTK speaks device pixels while Qt
        mouse events are logical ones. Getting it wrong puts every click
        hundreds of pixels from where it was aimed.
        """
        window = self._viewport.render_window
        logical = self._viewport.interactor.width()
        if window is None or not logical:
            return 1.0
        wide = window.GetSize()[0]
        return wide / logical if wide else 1.0

    def _select_at(self, x: float, y: float) -> None:
        """Pick up whatever was clicked, or put everything down."""
        self._modelling.select(self._body_at(x, y) or "")

    def _offer_the_scene_menu(self, x: float, y: float, at: QPoint) -> None:
        """Select what was right-clicked and ask what to do with it."""
        body = self._body_at(x, y)
        if body:
            self._modelling.select(body)
        self._scene_menu.exec(at)

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
        # Through signals, never directly. A direct callback runs on whichever
        # thread announced it, and a rebuild announces from a worker - see
        # _WindowSignals. Qt queues these onto the interface thread because
        # that is where this object lives.
        self._signals.cad_outcome.connect(self._on_cad_outcome)
        self._signals.model_changed.connect(self._on_model_changed)
        self._signals.kernel_answered.connect(self._on_kernel_answered)
        self._signals.state_changed.connect(self._on_state_changed)
        self._signals.notified.connect(self._on_notification)
        self._signals.busy_changed.connect(self._on_busy_changed)

        self._modelling.on_outcome(self._signals.cad_outcome.emit)
        self._modelling.on_state(self._signals.model_changed.emit)
        # The feature tree's own busy signal. Without it the status bar only
        # heard about the mesh workspace, so a rebuild - two seconds of OCCT -
        # ran in silence.
        self._modelling.on_busy(self._signals.busy_changed.emit)
        self._view_model.on_state_changed(self._signals.state_changed.emit)
        self._view_model.on_notification(self._signals.notified.emit)
        self._view_model.on_busy_changed(self._signals.busy_changed.emit)

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
            self._bringing_in_a_new_model = True
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

    def _open_an_earlier_model(self) -> None:
        """Offer back a model made earlier that was never saved.

        Generating from a picture takes about a minute and lands in a
        temporary file. Regenerating something that is still on the disk is a
        minute nobody should have to spend twice.
        """
        dialog = EarlierModelsDialog(self)
        if dialog.exec() and dialog.chosen is not None:
            self._bringing_in_a_new_model = True
            self._view_model.open(dialog.chosen.path)

    def _opened_from_gallery(self, download: Download) -> None:
        """Open a model that arrived from a repository, keeping its credit."""
        self._bringing_in_a_new_model = True
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

        wanted = dialog.how_many
        if wanted > 1:
            self.statusBar().showMessage(
                f"Making {wanted} different shapes from that picture. Each takes about a minute."
            )
            self._view_model.generate_variants(
                chosen, wanted, dialog.options(), self._on_generation_progress
            )
            return

        self.statusBar().showMessage("Making a model from that picture...")
        self._bringing_in_a_new_model = True
        self._view_model.generate_from_image(chosen, dialog.options(), self._on_generation_progress)

    def _from_photos(self) -> None:
        """Measure a model from several photographs.

        Deliberately worded as *measuring* rather than making, and put beside
        the single-picture entry rather than inside it. The two look similar
        and are not: one asks a model to invent a plausible back, and this one
        works out where the camera was and measures the shape it saw. Which of
        those produced a model is the whole question six months later, so the
        application never blurs them.
        """
        if not self._view_model.can_reconstruct:
            QMessageBox.information(
                self,
                "ModelPop",
                "Measuring a model from photographs is not set up.\n\n"
                + self._view_model.describe_reconstruction(),
            )
            return

        dialog = ReconstructDialog(self)
        if not dialog.exec() or not dialog.photos.is_usable:
            return

        self.statusBar().showMessage(
            f"Measuring a model from {len(dialog.photos)} photographs. This takes minutes."
        )
        self._bringing_in_a_new_model = True
        self._view_model.reconstruct_from_photos(
            dialog.photos, dialog.options, self._on_generation_progress
        )

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

    def _rescue_detail(self) -> None:
        """Ask how deep the relief should be, then bake it in.

        A number rather than a switch, because nobody can say in advance what
        the right depth is: luminance is a *guess* at height, and the only way
        to find out is to try it, look at it, and try again. The dialog says
        so rather than implying the number means something exact.
        """
        depth, chosen = QInputDialog.getDouble(
            self,
            "Rescue the detail",
            "How deep should the relief be?\n\n"
            "The model's colour is read as height, which is a guess rather than a\n"
            "measurement. Try a number, look at the result, and change it.",
            0.4,
            0.05,
            5.0,
            2,
        )
        if chosen:
            self._view_model.rescue_detail(depth)

    def _watch_the_printer(self) -> None:
        """Open the panel that keeps asking what the printer is doing."""
        connection = self._view_model.printer_connection
        problem = connection.problem
        if problem is not None:
            QMessageBox.information(self, "ModelPop", f"{problem}\n\nAdd it in File > Settings.")
            return

        MonitorDialog(self._view_model.printer_status, connection, self).show()

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
        self._draw_scene(has_problems=has_problems, fallback=state.mesh)
        self._scene.frame_model()

        # A new model is a new range for the cut, and a new actor with no
        # clipping on it. Both have to be reapplied or the section silently
        # stops working on everything opened after the first one.
        self._section.fits(None if state.mesh is None else state.mesh.bounds)
        if self._section_panel is not None:
            self._section_panel.refresh()
        self._scene.set_section(self._section.plane)

        # The handles were attached to the actor that has just been
        # replaced, so they have to go back on the new one or they hover
        # over a model they no longer move. They were also taken off while a
        # drag was being turned into features, so this is what puts them back.
        self._drag_in_flight = False
        if self._drag_action.isChecked():
            self._scene.start_dragging(self._dragged, self._dragging)

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

        # Queued rather than called: this is a state handler, and starting a
        # rebuild from inside one re-enters the announcement it is handling.
        QTimer.singleShot(0, lambda: self._take_whatever_arrived_into_the_scene(state))
        self._cad_panel.explain_instead(_where_it_came_from(state))
        self._how_button.setVisible(state.last_generation is not None)
        self._refresh_scene_menu()
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
        self._detail_button.setVisible(self._view_model.can_rescue_detail)
        self._variants.show_history(self._view_model.history)

    def _look_into_the_printer(self) -> None:
        """Put the view back to square on to the machine, and redraw."""
        self._scene.look_into_the_printer()
        self._viewport.render()
        self.statusBar().showMessage("Looking into the printer.", 4000)

    def _show_a_variant(self, index: int) -> None:
        """Put a different shape from the same run on the plate."""
        self._bringing_in_a_new_model = True
        self._view_model.show_variant(index)

    def _adopt_from_the_scene(self, mesh: object) -> None:
        """Hand the scene's geometry to the workspace, and remember doing it.

        Remembering is what lets a mesh arriving from outside be told apart
        from the scene's own output, which is otherwise the same event.
        """
        self._scene_hash = getattr(mesh, "content_hash", "") if mesh is not None else ""
        self._view_model.adopt(mesh)  # type: ignore[arg-type]

    def _take_whatever_arrived_into_the_scene(self, state: WorkspaceState) -> None:
        """Make a model that arrived whole into an object on the plate.

        Opened from a file, made from a picture, reconstructed from
        photographs: each used to leave the application holding a mesh and
        nothing else - visible, and impossible to select, move or do anything
        with. Each is now an object like any other.

        A mesh that the scene itself produced is skipped, or this would loop.
        """
        mesh = state.mesh
        if mesh is None or mesh.is_empty or self._modelling.is_busy:
            return
        if mesh.content_hash == self._scene_hash:
            return

        if self._bringing_in_a_new_model or not self._modelling.bodies:
            self._bringing_in_a_new_model = False
            self._modelling.place_mesh(mesh, _where_it_came_from_briefly(state))
        elif self._modelling.is_a_whole_mesh:
            # Repaired or simplified: the same object, reworked.
            self._modelling.rework_selected(mesh, "Rework the model")

    def _draw_scene(self, *, has_problems: bool = False, fallback: object = None) -> None:
        """Put the scene on screen, one actor per object.

        A model that came from the CAD tools is a scene of separate objects and
        is drawn as one; a mesh that was opened or generated is a single thing
        with no feature tree behind it, and is drawn as one actor as before.
        """
        bodies = self._modelling.bodies
        if bodies:
            self._scene.show_bodies(
                [(body.id, body.mesh) for body in bodies],
                self._modelling.selected,
                has_problems=has_problems,
            )
            return
        self._scene.show_mesh(fallback, has_problems=has_problems)  # type: ignore[arg-type]

    def kernel_is_being_probed_by(self, kernel: object) -> None:
        """Be told when the CAD kernel's availability is finally known.

        Asked for on its own thread so the window is not held shut for the two
        and a half seconds it takes; until it lands the CAD tools are greyed,
        and this is what un-greys them.
        """
        starter = getattr(kernel, "start_probing", None)
        if starter is not None:
            starter(self._signals.kernel_answered.emit)

    def _on_kernel_answered(self) -> None:
        """The kernel said whether it is there. Offer what it allows."""
        self._cad_panel.refresh_what_is_possible()
        self._refresh_buttons()

    def _on_model_changed(self, _state: object) -> None:
        """Redraw when the scene, or which object is selected, changes.

        Selection changes nothing about the geometry, so the workspace never
        hears about it - but the viewport has to, or the thing the toolbar is
        pointed at is invisible.
        """
        report = self._view_model.state.readiness
        self._draw_scene(
            has_problems=report is not None and not report.is_printable,
            fallback=self._view_model.state.mesh,
        )
        if self._drag_action.isChecked():
            self._scene.start_dragging(self._dragged, self._dragging)
        self._viewport.render()
        self._refresh_scene_menu()
        QTimer.singleShot(0, self._reseat_if_asked)

    def _on_cad_outcome(self, outcome: Outcome) -> None:
        """Report what a CAD command did.

        A refused command is shown in the status bar rather than a dialog: the
        model is untouched and still correct, so interrupting the user with a
        modal would overstate it.
        """
        message = f"{outcome.message}. {outcome.detail}" if outcome.detail else outcome.message
        # How long the rebuild actually took. Without it a slow one is a
        # complaint nobody can act on; with it, it is a number.
        spent = self._modelling.state.rebuild_seconds
        if not outcome.refused and spent >= SAY_HOW_LONG_ABOVE_SECONDS:
            message = f"{message} ({spent:.1f}s)"
        self.statusBar().showMessage(message, 10000)

        if outcome.refused and self._drag_in_flight:
            # A refused command changes nothing, so no new geometry is coming
            # to replace the actor the drag moved. Put it back, or the part is
            # left standing somewhere the model does not agree with.
            self._drag_in_flight = False
            self._scene.forget_drag()
            if self._drag_action.isChecked():
                self._scene.start_dragging(self._dragged, self._dragging)
            self._viewport.render()
        # The CAD tools can create a model without the workspace changing, so
        # the menu has to be refreshed from here too.
        self._save_project_action.setEnabled(self._modelling.can_save)

    def _on_notification(self, notification: Notification) -> None:
        self.statusBar().showMessage(notification.message, 8000)
        if notification.is_error:
            QMessageBox.warning(
                self, "ModelPop", f"{notification.message}\n\n{notification.detail}"
            )

    def _on_busy_changed(self, _busy: bool) -> None:
        """Say what is happening, from whichever half of the app is doing it.

        Two view-models work independently - the feature tree and the mesh
        workspace - and either can be busy. Reading only one of them meant
        repairing a million triangles, which is a minute and a half, said
        nothing at all: people click again, and the second click is either
        refused or applied twice.
        """
        self._say_what_is_happening()

    def _say_what_is_happening(self) -> None:
        """Put whatever is running in the status bar, or clear the cursor."""
        doing = self._modelling.doing or self._view_model.doing
        busy = self._modelling.is_busy or self._view_model.is_busy
        self.setCursor(Qt.CursorShape.WaitCursor if busy else Qt.CursorShape.ArrowCursor)
        if busy and doing:
            self.statusBar().showMessage(f"{doing}...")


def _where_it_came_from(state: WorkspaceState) -> str:
    """What to show instead of a feature tree, for a model that has none.

    A mesh that arrived whole has no steps behind it, so the tree is empty -
    which reads as broken rather than as "there is nothing to show". This says
    where it came from and what can still be done to it.
    """
    if not state.has_model:
        return ""

    came_from = state.generation_note or (
        f"Opened from {state.source_path.name}." if state.source_path else "Arrived whole."
    )
    return (
        f"{came_from}\n\n"
        "It has no steps behind it, so there is nothing to list here. "
        "Right-click it in the viewport to drop it on the bed, resize it, "
        "repair it or simplify it.\n\n"
        "Start a shape above to build something that does have a tree."
    )


def _where_it_came_from_briefly(state: WorkspaceState) -> str:
    """What to call an object that arrived whole, in a few words."""
    if state.generation_note:
        return state.generation_note.split(".")[0][:60]
    if state.source_path is not None:
        return state.source_path.stem[:60]
    return "The model"
