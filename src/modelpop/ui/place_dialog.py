"""Moving and turning a part without having to hit anything with the mouse.

The drag handles in the viewport work, and they are still there. They are also
the wrong tool for most of what people actually want: land exactly on the bed,
shift it by five millimetres, turn it a quarter turn. Doing any of those by eye
with a gizmo is fiddly at best, and on a part whose handles start inside its own
geometry it feels broken even when it is not.

So this is the same operations with buttons on them. Every one goes through the
command bus as a ``Move`` or a ``Rotate``, joins the feature tree, and undoes -
there is no second path into the model (ADR-0001), which is why this panel can
be this simple.

A tool window, not a modal dialog: placing a part means looking at it between
nudges, and an OK button would make every look cost a round trip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modelpop.domain.placement import centre_over_bed, nudge, settle_onto_bed

if TYPE_CHECKING:
    from modelpop.application.modelling import ModelState
    from modelpop.presentation.modelling_view_model import ModellingViewModel

__all__ = ["PlaceDialog"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"
_READING_STYLE = "color: #C5CDD6; font-family: Consolas, monospace;"

# Whole millimetres mostly, with a tenth for the last bit of fitting. 5 mm is
# the default because it is visible on screen at the zoom a whole part is
# usually viewed at - 1 mm nudges look like nothing is happening.
_STEPS = (0.1, 0.5, 1.0, 5.0, 10.0, 25.0)
_DEFAULT_STEP = 5.0

_TURNS = (-90, -45, 45, 90)

_AXES = [("Upright (Z)", "Z"), ("Left to right (X)", "X"), ("Front to back (Y)", "Y")]


class _PanelSignals(QObject):
    """Announcements from the view-model, marshalled onto this thread.

    The same bridge ``CadPanel`` uses, and for the same reason: a rebuild
    finishes on a worker and tells its listeners from there. Touching a widget
    from that thread is undefined, and in practice the panel simply stops
    updating.
    """

    state_changed = Signal(object)
    busy_changed = Signal(bool)


class PlaceDialog(QDialog):
    """Nudge, turn, drop and centre - all of it as steps in the tree."""

    def __init__(self, view: ModellingViewModel, parent: QWidget | None = None) -> None:
        """Build the panel around the modelling view-model."""
        super().__init__(parent)
        self._view = view
        self.setWindowTitle("Move and turn it")

        layout = QVBoxLayout(self)
        self._reading = QLabel()
        self._reading.setStyleSheet(_READING_STYLE)
        layout.addWidget(self._reading)
        layout.addWidget(self._move_box())
        layout.addWidget(self._turn_box())
        layout.addWidget(self._place_box())

        hint = QLabel(
            "Arrow keys move it left, right, front and back. Page Up and Page "
            "Down raise and lower it. Every nudge is a step in the tree, so "
            "Undo takes it back."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(_HINT_STYLE)
        layout.addWidget(hint)

        self._signals = _PanelSignals()
        self._signals.state_changed.connect(self._show)
        self._signals.busy_changed.connect(lambda _: self._refresh())
        self._view.on_state(self._signals.state_changed.emit)
        self._view.on_busy(self._signals.busy_changed.emit)
        self._show(self._view.state)

    # ------------------------------------------------------------ the controls

    def _move_box(self) -> QGroupBox:
        """Six directions and the distance each one covers."""
        box = QGroupBox("Move it")
        layout = QVBoxLayout(box)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("By"))
        self._step = QComboBox()
        for size in _STEPS:
            self._step.addItem(f"{size:g} mm", size)
        self._step.setCurrentIndex(_STEPS.index(_DEFAULT_STEP))
        step_row.addWidget(self._step)
        step_row.addStretch(1)
        layout.addLayout(step_row)

        # Laid out the way the part sits on the plate seen from above, with
        # up and down beside it. Labelling them by axis alone ("+Y") is
        # accurate and tells nobody which way the part will go.
        grid = QGridLayout()
        self._buttons: list[QPushButton] = []
        for text, axis, sign, row, column in (
            ("↑ Back", "Y", 1.0, 0, 1),
            ("← Left", "X", -1.0, 1, 0),
            ("→ Right", "X", 1.0, 1, 2),
            ("↓ Front", "Y", -1.0, 2, 1),
            ("⬆ Up", "Z", 1.0, 0, 3),
            ("⬇ Down", "Z", -1.0, 2, 3),
        ):
            button = QPushButton(text)
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(400)
            button.setAutoRepeatInterval(250)
            button.clicked.connect(lambda _=False, a=axis, s=sign: self._nudge(a, s))
            grid.addWidget(button, row, column)
            self._buttons.append(button)
        layout.addLayout(grid)
        return box

    def _turn_box(self) -> QGroupBox:
        """Quarter and eighth turns, and anything else typed in."""
        box = QGroupBox("Turn it")
        layout = QVBoxLayout(box)

        axis_row = QHBoxLayout()
        axis_row.addWidget(QLabel("About"))
        self._axis = QComboBox()
        for label, axis in _AXES:
            self._axis.addItem(label, axis)
        axis_row.addWidget(self._axis)
        axis_row.addStretch(1)
        layout.addLayout(axis_row)

        turn_row = QHBoxLayout()
        for degrees in _TURNS:
            button = QPushButton(f"{degrees:+d}°")
            button.clicked.connect(lambda _=False, d=degrees: self._turn(float(d)))
            turn_row.addWidget(button)
            self._buttons.append(button)

        self._degrees = QDoubleSpinBox()
        self._degrees.setRange(-360.0, 360.0)
        self._degrees.setDecimals(1)
        self._degrees.setSuffix(" °")
        self._degrees.setValue(15.0)
        turn_row.addWidget(self._degrees)

        by = QPushButton("Turn")
        by.clicked.connect(lambda: self._turn(self._degrees.value()))
        turn_row.addWidget(by)
        self._buttons.append(by)
        layout.addLayout(turn_row)
        return box

    def _place_box(self) -> QGroupBox:
        """The two placements worth a button of their own."""
        box = QGroupBox("Put it somewhere sensible")
        layout = QHBoxLayout(box)

        self._drop_button = QPushButton("Drop it on the bed")
        self._drop_button.setToolTip(
            "Lands the lowest point of the part on the plate, whether it is "
            "floating above it or sunk through it."
        )
        self._drop_button.clicked.connect(self._drop)
        layout.addWidget(self._drop_button)

        self._centre_button = QPushButton("Centre it on the plate")
        self._centre_button.setToolTip("Leaves the height alone.")
        self._centre_button.clicked.connect(self._centre)
        layout.addWidget(self._centre_button)

        self._buttons.extend([self._drop_button, self._centre_button])
        return box

    # ------------------------------------------------------------- the actions

    def _nudge(self, axis: str, sign: float) -> None:
        """One step along an axis, in the direction the button names."""
        step = float(self._step.currentData())
        move = nudge(axis, step * sign)
        self._view.move(move.dx, move.dy, move.dz)

    def _turn(self, degrees: float) -> None:
        """Rotate about the chosen axis."""
        if degrees:
            self._view.rotate(degrees, str(self._axis.currentData()))

    def _drop(self) -> None:
        """Settle the part onto the plate, if it is not already there."""
        self._apply_placement(settle_onto_bed, "It is already on the bed.")

    def _centre(self) -> None:
        """Slide the part over the middle of the plate."""
        self._apply_placement(centre_over_bed, "It is already centred.")

    def _apply_placement(self, work: object, nothing_to_do: str) -> None:
        """Run one of the placement calculations against the built part.

        Both need the part's actual bounding box, which only exists once it
        has been built - so both say why rather than doing nothing when there
        is no geometry yet.
        """
        mesh = self._view.state.mesh
        if mesh is None or mesh.is_empty:
            self._reading.setText("Nothing built yet.")
            return

        move = work(mesh.bounds)  # type: ignore[operator]
        if not any((move.dx, move.dy, move.dz)):
            self._reading.setText(nothing_to_do)
            return
        self._view.move(move.dx, move.dy, move.dz)

    # ------------------------------------------------------------- the display

    def _show(self, state: ModelState) -> None:
        """Say where the part is now, in the terms the buttons use."""
        mesh = state.mesh
        if mesh is None or mesh.is_empty:
            self._reading.setText("Nothing built yet.")
        else:
            box = mesh.bounds
            centre_x, centre_y, _ = box.centre
            seated = "on the bed" if abs(box.min_z) < 0.01 else f"{box.min_z:+.2f} mm from the bed"
            self._reading.setText(f"centre {centre_x:+.1f}, {centre_y:+.1f} mm — bottom {seated}")
        self._refresh()

    def _refresh(self) -> None:
        """Nothing here works on an unbuilt part or during a rebuild."""
        usable = self._view.can_operate and self._view.can_build and not self._view.is_busy
        for button in self._buttons:
            button.setEnabled(usable)

    # ------------------------------------------------------------- the keyboard

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Arrow keys for the plate, Page Up and Down for height.

        The user tried the cursor keys on the viewport handles and nothing
        happened, which is fair - they are the obvious thing to try. Here they
        do what they look like they should.
        """
        moves = {
            Qt.Key.Key_Left: ("X", -1.0),
            Qt.Key.Key_Right: ("X", 1.0),
            Qt.Key.Key_Up: ("Y", 1.0),
            Qt.Key.Key_Down: ("Y", -1.0),
            Qt.Key.Key_PageUp: ("Z", 1.0),
            Qt.Key.Key_PageDown: ("Z", -1.0),
        }
        move = moves.get(Qt.Key(event.key()))
        if move is None or not self._drop_button.isEnabled():
            super().keyPressEvent(event)
            return
        axis, sign = move
        self._nudge(axis, sign)
        event.accept()
