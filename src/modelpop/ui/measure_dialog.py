"""Measuring a real size off a photograph.

Rob's original ask, in his own words: photos with a ruler in shot, for scale.
A picture has no scale of its own, so until now the app made a generated model
100 mm across and said out loud that the size was chosen rather than measured.
This is where it stops having to say that.

Two lines. Drag one along the ruler and type what it really measures, drag the
other across the subject, and similar triangles do the rest. No computer
vision: finding a ruler automatically fails silently and plausibly, and a model
that is 30% wrong looks completely reasonable right up until it meets a pair of
calipers. Two drags take five seconds and the user can see exactly what was
measured.

The arithmetic lives in ``modelpop.domain.photo_scale``, so everything here is
about drawing, clicking and saying what happened.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modelpop.domain.photo_scale import PhotoScale, Reference, length_of
from modelpop.domain.units import Length

if TYPE_CHECKING:
    from pathlib import Path

    from PySide6.QtGui import QMouseEvent, QPaintEvent

__all__ = ["Line", "MeasureDialog", "PhotoCanvas"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"
_REFERENCE_COLOUR = "#F2C14E"
_SUBJECT_COLOUR = "#9FC5E8"
_BACKDROP = "#1F2328"

# Things people have to hand, and what they really measure. A reference the
# user already owns beats one they have to print, which is why a bank card is
# on the list: it is 85.60 mm by international standard and never varies.
COMMON_REFERENCES: list[tuple[str, float]] = [
    ("Ruler, 150 mm", 150.0),
    ("Ruler, 300 mm", 300.0),
    ("Bank card, long edge", 85.6),
    ("A4 sheet, short edge", 210.0),
    ("UK pound coin", 23.43),
    ("UK ten pence", 24.5),
    ("Something else", 0.0),
]


class Line(Enum):
    """Which of the two measurements is being drawn."""

    REFERENCE = "the thing you know the size of"
    SUBJECT = "the thing you want to print"


class PhotoCanvas(QWidget):
    """The photograph, with two lines drawn on it.

    Drags, not clicks: a drag shows the line as it is being drawn, and a line
    you can see is a line you can correct. Both lines stay visible together so
    the ratio between them - which is the whole measurement - is on screen.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build an empty canvas."""
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self._photo: QPixmap | None = None
        self._drawing = Line.REFERENCE
        self._lines: dict[Line, tuple[tuple[float, float], tuple[float, float]]] = {}
        self._dragging_from: QPointF | None = None

    # ---------------------------------------------------------------- loading

    def show_photo(self, path: Path) -> bool:
        """Load a picture to measure on. False if it is not one."""
        photo = QPixmap(str(path))
        if photo.isNull():
            return False
        self._photo = photo
        self._lines.clear()
        self.update()
        return True

    def draw_next(self, which: Line) -> None:
        """Choose which line the next drag draws."""
        self._drawing = which
        self.update()

    @property
    def drawing(self) -> Line:
        """Which line the next drag will draw."""
        return self._drawing

    def clear(self) -> None:
        """Forget both lines and start again."""
        self._lines.clear()
        self._drawing = Line.REFERENCE
        self.changed.emit()
        self.update()

    # --------------------------------------------------------------- measuring

    def pixels_of(self, which: Line) -> float:
        """How long one of the lines is, in *photograph* pixels.

        Converted back out of the displayed size, because the photograph is
        scaled to fit the widget and a measurement in screen pixels would
        change every time the window was resized.
        """
        drawn = self._lines.get(which)
        if drawn is None or self._photo is None:
            return 0.0
        on_screen = length_of(*drawn)
        return on_screen / self._scale() if self._scale() else 0.0

    def set_line(self, which: Line, start: tuple[float, float], end: tuple[float, float]) -> None:
        """Place a line directly, in widget coordinates.

        Exists so the measurement can be driven without a mouse, which is how
        it is tested: synthesising drag events tests Qt, not this.
        """
        self._lines[which] = (start, end)
        self.changed.emit()
        self.update()

    # ---------------------------------------------------------------- the mouse

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Start a line."""
        self._dragging_from = QPointF(event.position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Show the line as it is drawn."""
        if self._dragging_from is None:
            return
        self._lines[self._drawing] = (
            (self._dragging_from.x(), self._dragging_from.y()),
            (event.position().x(), event.position().y()),
        )
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Finish the line and move on to the other one.

        Moving on automatically because the order is always the same, and a
        user who has just drawn the reference wants to draw the subject next
        without being asked which.
        """
        self.mouseMoveEvent(event)
        self._dragging_from = None
        if self._drawing is Line.REFERENCE and Line.SUBJECT not in self._lines:
            self._drawing = Line.SUBJECT
        self.update()

    # ---------------------------------------------------------------- painting

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the photograph and whichever lines exist."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(_BACKDROP))

        if self._photo is None:
            painter.setPen(QPen(QColor(_SUBJECT_COLOUR)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No picture loaded")
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(self._where_the_photo_goes(), self._photo, QRectF(self._photo.rect()))

        for which, colour in ((Line.REFERENCE, _REFERENCE_COLOUR), (Line.SUBJECT, _SUBJECT_COLOUR)):
            drawn = self._lines.get(which)
            if drawn is None:
                continue
            painter.setPen(QPen(QColor(colour), 3))
            start, end = drawn
            painter.drawLine(QPointF(*start), QPointF(*end))
            for point in (start, end):
                painter.drawEllipse(QPointF(*point), 4, 4)

    def _scale(self) -> float:
        """How much the photograph is shrunk to fit the widget."""
        if self._photo is None or self._photo.width() == 0:
            return 0.0
        return min(
            self.width() / self._photo.width(),
            self.height() / self._photo.height(),
        )

    def _where_the_photo_goes(self) -> QRectF:
        """The rectangle the photograph is painted into, centred."""
        if self._photo is None:
            return QRectF(self.rect())
        scale = self._scale()
        width, height = self._photo.width() * scale, self._photo.height() * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)


class MeasureDialog(QDialog):
    """Ask the user to measure the subject against something of known size."""

    def __init__(self, image: Path, parent: QWidget | None = None) -> None:
        """Build the dialog for one photograph."""
        super().__init__(parent)
        self.setWindowTitle("Measure it from the photo")
        self.setMinimumWidth(560)

        rows = QVBoxLayout(self)

        self._canvas = PhotoCanvas()
        self._loaded = self._canvas.show_photo(image)
        self._canvas.changed.connect(self._refresh)
        rows.addWidget(self._canvas)

        self._step = QLabel()
        self._step.setWordWrap(True)
        rows.addWidget(self._step)

        known = QHBoxLayout()
        known.addWidget(QLabel("The reference is a"))

        self._what = QComboBox()
        for label, _ in COMMON_REFERENCES:
            self._what.addItem(label)
        self._what.currentIndexChanged.connect(self._use_known_size)
        known.addWidget(self._what)

        self._real = QDoubleSpinBox()
        self._real.setRange(0.1, 5000.0)
        self._real.setValue(COMMON_REFERENCES[0][1])
        self._real.setSuffix(" mm")
        self._real.valueChanged.connect(self._refresh)
        known.addWidget(self._real)

        again = QPushButton("Draw both again")
        again.clicked.connect(self._canvas.clear)
        known.addWidget(again)
        rows.addLayout(known)

        self._answer = QLabel()
        self._answer.setWordWrap(True)
        self._answer.setStyleSheet(_HINT_STYLE)
        rows.addWidget(self._answer)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        rows.addWidget(self._buttons)

        self._refresh()

    # -------------------------------------------------------------- answering

    @property
    def loaded(self) -> bool:
        """Whether the picture could be read at all."""
        return self._loaded

    @property
    def scale(self) -> PhotoScale:
        """What the two drawn lines say the subject measures."""
        return PhotoScale(
            Reference(self._canvas.pixels_of(Line.REFERENCE), Length.mm(self._real.value())),
            self._canvas.pixels_of(Line.SUBJECT),
        )

    @property
    def canvas(self) -> PhotoCanvas:
        """The photograph being measured on."""
        return self._canvas

    # ---------------------------------------------------------------- display

    def _use_known_size(self, index: int) -> None:
        millimetres = COMMON_REFERENCES[index][1]
        if millimetres:
            self._real.setValue(millimetres)

    def _refresh(self) -> None:
        """Say which line is being drawn, and what the pair of them mean."""
        if not self._loaded:
            self._step.setText("That picture could not be read.")
            self._answer.setText("")
        else:
            drawing = self._canvas.drawing
            self._step.setText(
                f"<b>Drag a line along {drawing.value}.</b> "
                + (
                    "Amber is the reference; blue is the subject."
                    if drawing is Line.REFERENCE
                    else "Drag across the widest part, the way you would measure it."
                )
            )
            self._answer.setText(self.scale.describe())

        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self._loaded and self.scale.is_usable)
