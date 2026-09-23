"""The controls for cutting the view open.

A tool window rather than a modal dialog, and that is the whole design: the
useful thing about a section is dragging it through a part and watching the
inside go by. A modal dialog with an OK button would make that impossible -
every look would cost a round trip - and the model underneath is not being
changed, so there is nothing to confirm.

It owns no state. Every control reads and writes the ``SectionTool`` it was
given, which is where the rules about what a cut may do actually live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from modelpop.presentation.sectioning import Axis

if TYPE_CHECKING:
    from modelpop.presentation.sectioning import SectionTool

__all__ = ["SectionDialog"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"

# The slider counts in whole steps, so the plane moves in tenths of a
# millimetre over its whole travel however long that is. Finer than anybody
# can see and coarse enough to stay responsive.
_STEPS = 1000

_AXIS_CHOICES = [
    ("Left to right", Axis.X),
    ("Front to back", Axis.Y),
    ("Top to bottom", Axis.Z),
]


class SectionDialog(QDialog):
    """Where to cut, how far along, and which half to keep."""

    changed = Signal()
    """The cut moved. The window redraws; nothing else happens."""

    def __init__(self, tool: SectionTool, parent: QWidget | None = None) -> None:
        """Build the panel around a section tool."""
        super().__init__(parent)
        self._tool = tool
        self.setWindowTitle("Cut it open")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self._axis = QComboBox()
        for label, _ in _AXIS_CHOICES:
            self._axis.addItem(label)
        self._axis.setCurrentIndex([axis for _, axis in _AXIS_CHOICES].index(tool.axis))
        self._axis.currentIndexChanged.connect(self._choose_axis)
        top.addWidget(self._axis, stretch=1)

        self._flip = QPushButton("Other half")
        self._flip.setToolTip("Keep the half currently being cut away")
        self._flip.clicked.connect(self._flip_it)
        top.addWidget(self._flip)
        layout.addLayout(top)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, _STEPS)
        self._slider.valueChanged.connect(self._slide)
        layout.addWidget(self._slider)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._summary)

        hint = QLabel(
            "This cuts the view, not the model. Nothing here changes what is "
            "exported, sliced or saved."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(_HINT_STYLE)
        layout.addWidget(hint)

        self.refresh()

    # ------------------------------------------------------------------ input

    def _choose_axis(self, index: int) -> None:
        self._tool.cut_along(_AXIS_CHOICES[index][1])
        self.refresh()
        self.changed.emit()

    def _flip_it(self) -> None:
        self._tool.flip()
        self._describe()
        self.changed.emit()

    def _slide(self, step: int) -> None:
        low, high = self._tool.travel
        self._tool.move_to(low + (high - low) * step / _STEPS)
        self._describe()
        self.changed.emit()

    # ----------------------------------------------------------------- output

    def refresh(self) -> None:
        """Take the slider's range and position from the tool.

        Called when the model changes as well as when the axis does, because
        the travel is the model's size and a new model is a new range. The
        slider is blocked while it is moved so that setting it does not read
        back as the user having dragged it.
        """
        low, high = self._tool.travel
        span = high - low
        step = 0 if span <= 0 else round((self._tool.offset - low) / span * _STEPS)

        self._slider.blockSignals(True)
        self._slider.setValue(step)
        self._slider.setEnabled(span > 0)
        self._slider.blockSignals(False)
        self._describe()

    def _describe(self) -> None:
        self._summary.setText(self._tool.describe())
