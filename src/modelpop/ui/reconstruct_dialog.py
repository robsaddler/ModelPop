"""Choosing photographs, and what to make of them.

Two jobs. It collects a set of photographs and says what is wrong with them
*before* anything starts, because the alternative is finding out after several
minutes of work - and it explains the one thing everybody gets wrong the first
time, which is that photogrammetry needs overlap and texture rather than good
photographs.

Every rule shown here lives in ``PhotoSet``. This reads them out.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modelpop.application.reconstruction_ports import Quality, ReconstructionOptions
from modelpop.domain.photo_set import READABLE, PhotoSet
from modelpop.domain.units import Length

__all__ = ["ReconstructDialog"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"
_WARNING_STYLE = "color: #F2C14E; font-size: 11px;"

_FILTER = "Photographs (" + " ".join(f"*{suffix}" for suffix in sorted(READABLE)) + ")"

# What actually decides whether a capture works, in the order people get it
# wrong. Worth the space: a bad capture costs minutes to discover and there is
# nothing the software can do about it afterwards.
_ADVICE = (
    "Walk right round the subject, taking a photograph every 10-15 degrees. "
    "Each one needs to overlap its neighbours by well over half.",
    "Keep the subject filling the frame, and keep the lighting the same. "
    "Move yourself, not the subject - a turntable makes the background move "
    "instead and confuses the solver.",
    "Plain, shiny and transparent things do not reconstruct. If the surface "
    "has no texture to match, nothing here can find one.",
    "Put a ruler in shot if you want the size to be real. Photographs carry no "
    "scale, so otherwise it comes out whatever size you ask for.",
)


class ReconstructDialog(QDialog):
    """Ask for photographs and how hard to work on them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog."""
        super().__init__(parent)
        self.setWindowTitle("Build a model from photographs")
        self.setMinimumWidth(520)

        self._photos = PhotoSet()
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        add = QPushButton("Choose photographs...")
        add.clicked.connect(self._choose_files)
        row.addWidget(add)
        folder = QPushButton("Choose a folder...")
        folder.clicked.connect(self._choose_folder)
        row.addWidget(folder)
        row.addStretch(1)
        layout.addLayout(row)

        self._listing = QListWidget()
        self._listing.setMinimumHeight(140)
        layout.addWidget(self._listing)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._warning = QLabel()
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet(_WARNING_STYLE)
        layout.addWidget(self._warning)

        form = QFormLayout()
        self._quality = QComboBox()
        for quality in Quality:
            self._quality.addItem(quality.describe)
        self._quality.setCurrentIndex(list(Quality).index(Quality.NORMAL))
        form.addRow("Effort", self._quality)

        self._size = QDoubleSpinBox()
        self._size.setRange(1.0, 1000.0)
        self._size.setValue(100.0)
        self._size.setSuffix(" mm")
        self._size.setToolTip(
            "Photographs carry no scale, so the finished model is made this big "
            "at its largest. Measure it properly afterwards if it matters."
        )
        form.addRow("Make it", self._size)
        layout.addLayout(form)

        hint = QLabel("\n".join(f"• {line}" for line in _ADVICE))
        hint.setWordWrap(True)
        hint.setStyleSheet(_HINT_STYLE)
        layout.addWidget(hint)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._refresh()

    # ------------------------------------------------------------------ input

    def _choose_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "Choose photographs", "", _FILTER)
        if chosen:
            self.set_photos(PhotoSet.of(Path(name) for name in chosen))

    def _choose_folder(self) -> None:
        """A folder, because that is how a capture arrives off a camera."""
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder of photographs")
        if chosen:
            self.set_photos(PhotoSet.of(Path(chosen).iterdir()))

    def set_photos(self, photos: PhotoSet) -> None:
        """Adopt a set of photographs, however they were chosen."""
        self._photos = photos
        self._refresh()

    # ----------------------------------------------------------------- output

    @property
    def photos(self) -> PhotoSet:
        """The photographs chosen."""
        return self._photos

    @property
    def options(self) -> ReconstructionOptions:
        """What to ask of the reconstruction."""
        return ReconstructionOptions(
            quality=list(Quality)[self._quality.currentIndex()],
            size=Length.mm(float(self._size.value())),
        )

    # ---------------------------------------------------------------- display

    def _refresh(self) -> None:
        self._listing.clear()
        self._listing.addItems([p.name for p in self._photos.photos])

        self._summary.setText(self._photos.describe())

        # A refusal and a warning read differently on purpose: one blocks and
        # one does not, and a user who cannot tell them apart reads both as no.
        problem = self._photos.problem
        self._warning.setText(problem or "\n".join(self._photos.advice))
        self._warning.setStyleSheet(_WARNING_STYLE if problem else _HINT_STYLE)

        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self._photos.is_usable)
