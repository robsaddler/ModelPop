"""The CAD tools, and the feature tree they build.

Rob was explicit that the CAD tools live in the app rather than being a reason
to open something else. This is that toolbar.

A thin shell over ``ModellingViewModel``: every decision about what a command
means, what it is called and whether it is allowed right now was made there, so
this file reads as layout. The one thing it owns is that a rebuild runs off the
interface thread, because a rebuild is a subprocess and takes a second or two.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modelpop.application.modelling import ModelState
from modelpop.domain.cad_commands import EdgeSelector, Face
from modelpop.domain.units import Length
from modelpop.presentation.modelling_view_model import ModellingViewModel
from modelpop.ui.outline_dialog import OutlineDialog

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["CadPanel", "TextDialog", "ThreadedRebuilder"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"
_ASSISTANT_COLOUR = "#9FC5E8"
_SUPPRESSED_COLOUR = "#6B7280"

_EDGE_CHOICES = [
    ("All edges", EdgeSelector.ALL),
    ("Vertical edges", EdgeSelector.VERTICAL),
    ("Horizontal edges", EdgeSelector.HORIZONTAL),
    ("Top edges", EdgeSelector.TOP),
    ("Bottom edges", EdgeSelector.BOTTOM),
]

_FACE_CHOICES = [
    ("Front", Face.FRONT),
    ("Back", Face.BACK),
    ("Top", Face.TOP),
    ("Bottom", Face.BOTTOM),
    ("Left", Face.LEFT),
    ("Right", Face.RIGHT),
]


def _number(
    value: float, low: float = 0.1, high: float = 500.0, step: float = 1.0
) -> QDoubleSpinBox:
    """A spin box with sane bounds, because every one here is a millimetre."""
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setSingleStep(step)
    box.setValue(value)
    box.setSuffix(" mm")
    return box


class TextDialog(QDialog):
    """Ask for text to put on a face.

    Its own dialog because it has five settings and a toolbar row of five
    controls for one operation reads as clutter.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog."""
        super().__init__(parent)
        self.setWindowTitle("Text on a surface")
        self.setMinimumWidth(380)

        form = QFormLayout(self)
        self._text = QLineEdit()
        self._text.setPlaceholderText("MSI")
        form.addRow("Text", self._text)

        self._face = QComboBox()
        for label, _ in _FACE_CHOICES:
            self._face.addItem(label)
        form.addRow("Face", self._face)

        self._size = _number(12.0, 1.0, 200.0)
        form.addRow("Height", self._size)

        self._depth = _number(1.0, 0.2, 20.0, 0.1)
        form.addRow("Depth", self._depth)

        self._style = QComboBox()
        self._style.addItems(["Raised", "Engraved"])
        form.addRow("Style", self._style)

        hint = QLabel(
            "Raised text is a single filament change on an AMS, which is how you "
            "get a second colour without splitting the model."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(_HINT_STYLE)
        form.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @property
    def text(self) -> str:
        """What to write."""
        return self._text.text().strip()

    @property
    def face(self) -> Face:
        """Which face to write it on."""
        return _FACE_CHOICES[self._face.currentIndex()][1]

    @property
    def letter_height(self) -> float:
        """Letter height in millimetres.

        Not ``size``: ``QWidget.size()`` already exists and returns a QSize, so
        a property of that name would shadow it and mean something else.
        """
        return float(self._size.value())

    @property
    def relief_depth(self) -> float:
        """How far the text stands out, or cuts in.

        Not ``depth``, for the same reason - ``QPaintDevice.depth()`` is an int
        colour depth, and quietly replacing it is how a widget starts drawing
        wrong for reasons nobody can find.
        """
        return float(self._depth.value())

    @property
    def raised(self) -> bool:
        """Whether it stands proud rather than cutting in."""
        return self._style.currentIndex() == 0


class _PanelSignals(QObject):
    """Carries the view-model's announcements back to the interface thread."""

    state_changed = Signal(object)
    busy_changed = Signal(bool)


class CadPanel(QWidget):
    """The CAD toolbar and the feature tree."""

    def __init__(
        self,
        view_model: ModellingViewModel,
        parent: QWidget | None = None,
    ) -> None:
        """Build the panel around a view-model."""
        super().__init__(parent)
        self._view = view_model

        # The view-model announces from whichever thread did the work, and a
        # rebuild runs on a worker. Touching a widget from there is undefined
        # behaviour - in practice the tree silently stops updating and the
        # buttons stay disabled, with no error anywhere. Signals cross back to
        # the interface thread, which is what makes the updates land.
        self._signals = _PanelSignals()
        self._signals.state_changed.connect(self._show)
        self._signals.busy_changed.connect(lambda _: self._refresh())
        self._view.on_state(self._signals.state_changed.emit)
        self._view.on_busy(self._signals.busy_changed.emit)

        self._build()
        self._show(self._view.state)

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._build_shapes())
        layout.addWidget(self._build_operations())
        layout.addWidget(self._build_describe())
        layout.addWidget(self._build_tree(), stretch=1)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._status)

    def _build_shapes(self) -> QGroupBox:
        group = QGroupBox("Start a shape")
        rows = QVBoxLayout(group)

        # Built before the buttons that read them, because each button captures
        # them in a lambda. Added to the layout further down, where they belong
        # on screen.
        self._at_x = _number(0.0, -500.0, 500.0)
        self._at_y = _number(0.0, -500.0, 500.0)
        self._at_z = _number(0.0, -500.0, 500.0)
        self._cut = QCheckBox("cut it out")
        self._cut.setToolTip("Remove this shape from the part instead of adding it")

        box_row = QHBoxLayout()
        self._box_w = _number(40.0)
        self._box_d = _number(40.0)
        self._box_h = _number(40.0)
        for field in (self._box_w, self._box_d, self._box_h):
            box_row.addWidget(field)
        add_box = QPushButton("Box")
        add_box.clicked.connect(
            lambda: self._view.add_box(
                self._box_w.value(),
                self._box_d.value(),
                self._box_h.value(),
                self._placement(),
                cut=self._cut.isChecked(),
            )
        )
        box_row.addWidget(add_box)
        rows.addLayout(box_row)

        round_row = QHBoxLayout()
        self._cyl_r = _number(15.0)
        self._cyl_h = _number(30.0)
        round_row.addWidget(self._cyl_r)
        round_row.addWidget(self._cyl_h)
        add_cyl = QPushButton("Cylinder")
        add_cyl.clicked.connect(
            lambda: self._view.add_cylinder(
                self._cyl_r.value(),
                self._cyl_h.value(),
                self._placement(),
                cut=self._cut.isChecked(),
            )
        )
        round_row.addWidget(add_cyl)

        self._sphere_r = _number(20.0)
        round_row.addWidget(self._sphere_r)
        add_sphere = QPushButton("Sphere")
        add_sphere.clicked.connect(
            lambda: self._view.add_sphere(
                self._sphere_r.value(), self._placement(), cut=self._cut.isChecked()
            )
        )
        round_row.addWidget(add_sphere)
        rows.addLayout(round_row)

        place_row = QHBoxLayout()
        place_row.addWidget(QLabel("at"))
        for field in (self._at_x, self._at_y, self._at_z):
            place_row.addWidget(field)
        place_row.addWidget(self._cut)
        rows.addLayout(place_row)

        note = QLabel(
            "A second shape is added to the first, not put in its place. Tick "
            "“cut it out” to make a hole or a pocket instead."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        rows.addWidget(note)
        return group

    def _build_operations(self) -> QGroupBox:
        group = QGroupBox("Change it")
        rows = QVBoxLayout(group)

        blend_row = QHBoxLayout()
        self._blend_size = _number(2.0, 0.1, 200.0, 0.5)
        blend_row.addWidget(self._blend_size)

        self._blend_edges = QComboBox()
        for label, _ in _EDGE_CHOICES:
            self._blend_edges.addItem(label)
        blend_row.addWidget(self._blend_edges)

        self._fillet_button = QPushButton("Round")
        self._fillet_button.clicked.connect(
            lambda: self._view.fillet(self._blend_size.value(), self._chosen_edges())
        )
        blend_row.addWidget(self._fillet_button)

        self._chamfer_button = QPushButton("Bevel")
        self._chamfer_button.clicked.connect(
            lambda: self._view.chamfer(self._blend_size.value(), self._chosen_edges())
        )
        blend_row.addWidget(self._chamfer_button)
        rows.addLayout(blend_row)

        hollow_row = QHBoxLayout()
        self._wall = _number(2.0, 0.4, 50.0, 0.2)
        hollow_row.addWidget(QLabel("Wall"))
        hollow_row.addWidget(self._wall)

        self._opening = QComboBox()
        self._opening.addItem("Sealed")
        for label, _ in _FACE_CHOICES:
            self._opening.addItem(f"Open at the {label.lower()}")
        hollow_row.addWidget(self._opening)

        self._hollow_button = QPushButton("Hollow")
        self._hollow_button.clicked.connect(self._hollow)
        hollow_row.addWidget(self._hollow_button)
        rows.addLayout(hollow_row)

        size_row = QHBoxLayout()
        self._target = _number(150.0, 1.0, 1000.0, 5.0)
        size_row.addWidget(QLabel("Make it"))
        size_row.addWidget(self._target)
        size_row.addWidget(QLabel("tall"))

        self._scale_button = QPushButton("Resize")
        self._scale_button.clicked.connect(
            lambda: self._view.scale_to(Length.mm(self._target.value()))
        )
        size_row.addWidget(self._scale_button)

        self._text_button = QPushButton("Text...")
        self._text_button.clicked.connect(self._add_text)
        size_row.addWidget(self._text_button)

        self._outline_button = QPushButton("Outline...")
        self._outline_button.setToolTip(
            "Draw a closed profile and give it thickness - a bracket, a gasket, "
            "a nameplate, anything with a constant cross-section"
        )
        self._outline_button.clicked.connect(self._add_outline)
        size_row.addWidget(self._outline_button)
        rows.addLayout(size_row)

        return group

    def _build_describe(self) -> QGroupBox:
        """Say what to change, in words.

        The model replies with the same operations the buttons above emit, so
        what it does lands in the same tree and undoes the same way. It is not
        a separate mode.
        """
        group = QGroupBox("Or just say what you want")
        rows = QVBoxLayout(group)

        row = QHBoxLayout()
        self._instruction = QLineEdit()
        self._instruction.setPlaceholderText("round the corners and hollow it out")
        self._instruction.returnPressed.connect(self._describe_a_change)
        row.addWidget(self._instruction, stretch=1)

        self._describe_button = QPushButton("Change it")
        self._describe_button.clicked.connect(self._describe_a_change)
        row.addWidget(self._describe_button)
        rows.addLayout(row)

        note = QLabel(
            "Whatever it changes appears in the list below and undoes like anything else."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        rows.addWidget(note)
        return group

    def _build_tree(self) -> QGroupBox:
        group = QGroupBox("How it was built")
        rows = QVBoxLayout(group)

        self._tree = QListWidget()
        self._tree.setAlternatingRowColors(True)
        rows.addWidget(self._tree)

        history_row = QHBoxLayout()
        self._undo_button = QPushButton("Undo")
        self._undo_button.clicked.connect(self._view.undo)
        history_row.addWidget(self._undo_button)

        self._redo_button = QPushButton("Redo")
        self._redo_button.clicked.connect(self._view.redo)
        history_row.addWidget(self._redo_button)

        history_row.addStretch(1)

        new_model = QPushButton("Start again")
        new_model.clicked.connect(self._view.clear)
        history_row.addWidget(new_model)
        rows.addLayout(history_row)

        self._split_button = QPushButton("Split into two colours...")
        self._split_button.setToolTip(
            "Write the body and the raised lettering as separate files, so a "
            "slicer can give each its own filament"
        )
        self._split_button.clicked.connect(self._split_colours)
        rows.addWidget(self._split_button)

        return group

    # --------------------------------------------------------------- commands

    def _placement(self) -> tuple[float, float, float]:
        """Where the next shape goes, measured from the centre of the part."""
        return (self._at_x.value(), self._at_y.value(), self._at_z.value())

    def _chosen_edges(self) -> EdgeSelector:
        return _EDGE_CHOICES[self._blend_edges.currentIndex()][1]

    def _hollow(self) -> None:
        index = self._opening.currentIndex()
        opening = None if index == 0 else _FACE_CHOICES[index - 1][1]
        self._view.hollow(self._wall.value(), opening)

    def _split_colours(self) -> None:
        """Ask where the two files should go, then write them."""
        directory = QFileDialog.getExistingDirectory(self, "Where should the two parts go?")
        if directory:
            self._view.split_colours(Path(directory))

    def _describe_a_change(self) -> None:
        self._view.describe_a_change(self._instruction.text())
        self._instruction.clear()

    def _add_outline(self) -> None:
        """Draw a profile and extrude it.

        Offered even on an empty model, because an outline is as good a way to
        start a part as a box is. A cut still needs something to cut into, and
        the view-model is what says so.
        """
        dialog = OutlineDialog(self)
        if not dialog.exec() or not dialog.points:
            return
        self._view.extrude(
            dialog.points,
            dialog.thickness,
            dialog.plane,
            cut=dialog.cut,
        )

    def _add_text(self) -> None:
        dialog = TextDialog(self)
        if not dialog.exec() or not dialog.text:
            return
        self._view.add_text(
            dialog.text,
            dialog.face,
            dialog.letter_height,
            dialog.relief_depth,
            raised=dialog.raised,
        )

    # ---------------------------------------------------------------- display

    def _show(self, state: ModelState) -> None:
        self._tree.clear()
        for line in state.features:
            item = QListWidgetItem(f"{line.index + 1}. {line.label}")
            if line.by_the_assistant:
                item.setToolTip("Asked for by the assistant")
                item.setForeground(QColor(_ASSISTANT_COLOUR))
            if line.suppressed or not line.understood:
                item.setForeground(QColor(_SUPPRESSED_COLOUR))
            self._tree.addItem(item)

        self._status.setText(state.describe())
        self._refresh()

    def _refresh(self) -> None:
        """Enable only what the model's current state actually allows."""
        buildable = self._view.can_build and not self._view.is_busy
        operable = self._view.can_operate and self._view.can_build

        for button in (
            self._fillet_button,
            self._chamfer_button,
            self._hollow_button,
            self._scale_button,
            self._text_button,
        ):
            button.setEnabled(operable)

        # An outline is a way to *start* a part, not only to change one, so it
        # follows the shape buttons rather than the operations.
        self._outline_button.setEnabled(buildable)

        self._describe_button.setEnabled(self._view.can_describe_a_change)
        self._instruction.setEnabled(self._view.can_describe_a_change)
        self._split_button.setEnabled(self._view.can_split_colours)
        self._undo_button.setEnabled(self._view.can_undo)
        self._redo_button.setEnabled(self._view.can_redo)
        self._undo_button.setToolTip(self._view.state.undo_label)
        self._redo_button.setToolTip(self._view.state.redo_label)

        if not buildable and not self._view.is_busy:
            self._status.setText("The CAD kernel is unavailable, so the model cannot be rebuilt.")


class ModellingWorker(QObject):
    """Runs one rebuild on a thread and says when it is finished."""

    done = Signal()

    def __init__(self, work: Callable[[], None]) -> None:
        super().__init__()
        self._work = work

    def run(self) -> None:
        """Do the work, and report finishing even if it raised."""
        try:
            self._work()
        finally:
            self.done.emit()


class ThreadedRebuilder:
    """Keeps the window responsive while OCCT works.

    A rebuild is a subprocess taking a second or two. Running it on the
    interface thread freezes the window for exactly as long, which reads as a
    crash.

    Both the thread **and** the worker are held for as long as the work lasts.
    Keeping only the thread is the obvious version and it silently does nothing:
    the worker has no parent, so it is collected the moment this returns, and
    the queued ``started`` connection dies with it. The thread then starts, runs
    an empty event loop, and waits forever. No exception, no output, no clue.
    This was written that way first and cost an hour.
    """

    def __init__(self, owner: QWidget) -> None:
        """Hold threads and their workers for as long as they run."""
        self._owner = owner
        self._live: list[tuple[QThread, ModellingWorker]] = []

    def __call__(self, work: Callable[[], None]) -> None:
        """Start one rebuild on its own thread."""
        thread = QThread(self._owner)
        worker = ModellingWorker(work)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: self._forget(thread))

        self._live.append((thread, worker))
        thread.start()

    def _forget(self, thread: QThread) -> None:
        self._live = [pair for pair in self._live if pair[0] is not thread]

    @property
    def running(self) -> int:
        """How many rebuilds are in flight. For tests, and for diagnostics."""
        return len(self._live)
