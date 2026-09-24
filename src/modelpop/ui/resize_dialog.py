"""Making an object bigger or smaller.

Buttons first. Resizing by hand is "a bit bigger", "half that", "double it" -
proportions, judged by eye against everything else on the plate - and making
somebody compute a millimetre figure to express that is the kind of rigidity
this application has been called out for. The typed size is still there, below,
for when the number is the point: a part that has to be exactly 60 mm.

A tool window rather than a modal dialog, and for the same reason as the move
panel: resizing means looking at the result and going again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modelpop.domain.units import Length

if TYPE_CHECKING:
    from modelpop.application.modelling import ModelState
    from modelpop.presentation.modelling_view_model import ModellingViewModel

__all__ = ["ResizeObjectDialog"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"
_READING_STYLE = "color: #C5CDD6; font-family: Consolas, monospace;"

# Halves and doubles, plus a nudge either way. Enough to get anywhere in a few
# clicks without ever typing a number.
_STEPS = ((0.5, "Half"), (0.9, "-10%"), (1.1, "+10%"), (2.0, "Double"))


class _PanelSignals(QObject):
    """The view-model's announcements, marshalled onto this thread."""

    state_changed = Signal(object)
    busy_changed = Signal(bool)


class ResizeObjectDialog(QDialog):
    """Scale the selected object, by eye or by number."""

    def __init__(self, view: ModellingViewModel, parent: QWidget | None = None) -> None:
        """Build the panel around the modelling view-model."""
        super().__init__(parent)
        self._view = view
        self.setWindowTitle("Resize it")

        layout = QVBoxLayout(self)
        self._reading = QLabel()
        self._reading.setStyleSheet(_READING_STYLE)
        layout.addWidget(self._reading)
        layout.addWidget(self._by_eye())
        layout.addWidget(self._by_number())

        hint = QLabel(
            "Scales the whole object about its height, so it keeps its "
            "proportions. It joins the feature tree and undoes like anything "
            "else."
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

    def _by_eye(self) -> QGroupBox:
        """The way anybody actually resizes something."""
        box = QGroupBox("Bigger or smaller")
        row = QHBoxLayout(box)
        self._buttons: list[QPushButton] = []
        for factor, label in _STEPS:
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, f=factor: self._view.scale_selected_by(f))
            row.addWidget(button)
            self._buttons.append(button)
        return box

    def _by_number(self) -> QGroupBox:
        """For when the number is the point."""
        box = QGroupBox("Or to an exact size")
        row = QHBoxLayout(box)
        row.addWidget(QLabel("Make it this tall"))

        self._typed = QLineEdit()
        self._typed.setPlaceholderText("60 mm, or 6 inches")
        self._typed.textChanged.connect(self._check)
        self._typed.returnPressed.connect(self._apply_typed)
        row.addWidget(self._typed)

        self._apply = QPushButton("Resize")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self._apply_typed)
        row.addWidget(self._apply)
        return box

    # ------------------------------------------------------------- the actions

    def _understood(self) -> Length | None:
        """What was typed, if it is a size at all."""
        try:
            return Length.parse(self._typed.text())
        except ValueError:
            return None

    def _check(self, _text: str) -> None:
        """Enable the button only for something that parses to a real size."""
        size = self._understood()
        usable = size is not None and size.millimetres > 0
        self._apply.setEnabled(bool(usable) and self._view.selected_body is not None)

    def _apply_typed(self) -> None:
        size = self._understood()
        if size is not None and size.millimetres > 0:
            self._view.scale_selected_to(size)

    # ------------------------------------------------------------- the display

    def _show(self, state: ModelState) -> None:
        """Say how big the selected object is now."""
        body = state.body(state.selected)
        if body is None:
            self._reading.setText("Nothing is selected.")
        else:
            box = body.bounds
            self._reading.setText(
                f"{body.label}: {box.width.format(places=1)} x "
                f"{box.depth.format(places=1)} x {box.height.format(places=1)}"
            )
        self._refresh()

    def _refresh(self) -> None:
        """Nothing here works without an object, or during a rebuild."""
        usable = self._view.selected_body is not None and not self._view.is_busy
        for button in self._buttons:
            button.setEnabled(usable)
        self._typed.setEnabled(usable)
        self._check(self._typed.text())
