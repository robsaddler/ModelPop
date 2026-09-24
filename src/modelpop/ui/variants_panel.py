"""The shapes made this session, and going back to one of them.

Deliberately a list rather than a grid of thumbnails. A hundred-pixel picture
of a grey mesh tells you almost nothing about whether it will print; the real
viewport, which you can orbit, tells you everything. So clicking a row puts
that shape in the viewport at full size, and the list carries the facts worth
comparing two candidates on - how many triangles, how big, which seed.

Switching between them costs nothing. They are all still in memory, which is
the only reason comparing them is worth doing at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from modelpop.presentation.variants import GenerationHistory

__all__ = ["VariantsPanel"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"


class VariantsPanel(QWidget):
    """A row per shape, newest first."""

    chosen = Signal(int)
    """A row was picked. The window puts that shape in the viewport."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the panel."""
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        heading = QLabel("Shapes made this session")
        heading.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(heading)

        self._listing = QListWidget()
        self._listing.setWordWrap(True)
        self._listing.setSpacing(4)
        self._listing.setStyleSheet(
            "QListWidget { border: none; } QListWidget::item { padding: 6px 4px; }"
        )
        # currentRowChanged rather than itemClicked, so arrow keys work too -
        # which is what anybody comparing three shapes actually reaches for.
        self._listing.currentRowChanged.connect(self._picked)
        layout.addWidget(self._listing, stretch=1)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._summary)

    def _picked(self, row: int) -> None:
        if row >= 0:
            self.chosen.emit(row)

    def show_history(self, history: GenerationHistory) -> None:
        """Redraw from the history.

        The selection is set without signalling. Letting it round-trip would
        put the shape back in the viewport on every redraw, including the
        redraw caused by having just put it there.
        """
        self._listing.blockSignals(True)
        self._listing.clear()
        for variant in history:
            item = QListWidgetItem(f"{variant.title}\n{variant.subtitle}")
            item.setToolTip(variant.describe())
            self._listing.addItem(item)

        index = history.chosen_index
        if index is not None:
            self._listing.setCurrentRow(index)
        self._listing.blockSignals(False)

        self._summary.setText(history.describe())
        self.setVisible(not history.is_empty)
