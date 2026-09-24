"""Picking a model the application made earlier and never saved.

A minute of generation that ended in a temporary file is worth offering back,
and the alternative - regenerating something that is still on the disk - is
what prompted this.

Modal, unlike the tool windows: this is one choice, made once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modelpop.presentation.earlier_models import EarlierModel, found_in, where_they_land

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["EarlierModelsDialog"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"


class EarlierModelsDialog(QDialog):
    """A list of models left behind by earlier runs."""

    def __init__(self, parent: QWidget | None = None, directory: Path | None = None) -> None:
        """Build the dialog around whatever is on the disk right now."""
        super().__init__(parent)
        self.setWindowTitle("Models made earlier")
        self.setMinimumWidth(520)

        self._found = found_in(directory or where_they_land())

        layout = QVBoxLayout(self)
        if self._found:
            layout.addWidget(
                QLabel(f"{len(self._found)} models from earlier runs are still on the disk.")
            )
        else:
            layout.addWidget(QLabel("Nothing from an earlier run is still on the disk."))

        self._list = QListWidget()
        for model in self._found:
            item = QListWidgetItem(model.describe())
            item.setToolTip(str(model.path))
            self._list.addItem(item)
        if self._found:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self._list)

        note = QLabel(
            "These live in the system's temporary folder, which Windows clears "
            "from time to time. Open the one you want and save it somewhere of "
            "your own to keep it."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).setEnabled(bool(self._found))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def chosen(self) -> EarlierModel | None:
        """Whichever model was picked, if any."""
        row = self._list.currentRow()
        if 0 <= row < len(self._found):
            return self._found[row]
        return None
