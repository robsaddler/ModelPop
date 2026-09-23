"""Drawing an outline, and giving it thickness.

Extrusion is the operation CAD exists for, and the one thing a box, a cylinder
and a sphere cannot between them describe. A bracket, a gasket, a nameplate, the
side of a case - all of them are a profile with a constant cross-section.

A full sketcher is a canvas, snapping, dimensions and a constraint solver, and
it is a project of its own. This is the useful part of it: corners typed or
pasted as coordinates, with a preview so nobody is drawing blind, and presets
for the shapes people actually start from. The parsing is a plain function, so
every rule about what counts as an outline is tested without a display.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QWidget,
)

from modelpop.domain.cad_commands import MIN_OUTLINE_POINTS, Plane

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

__all__ = [
    "SPIN_PRESETS",
    "Operation",
    "OutlineDialog",
    "OutlinePreview",
    "describe_outline",
    "describe_profile",
    "enclosed_area",
    "parse_outline",
]


class Operation(Enum):
    """What to do with a profile once it is drawn."""

    EXTRUDE = "give it thickness"
    REVOLVE = "spin it round"


_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"
_OUTLINE_COLOUR = "#9FC5E8"
_CORNER_COLOUR = "#E8E8E8"
_FILL_COLOUR = QColor(159, 197, 232, 60)

# Below this an outline is a line, not a shape. In square millimetres, so this
# is a tenth of a millimetre square - far smaller than anything printable, and
# large enough to catch corners that are all collinear.
MIN_AREA_MM2 = 1.0

_PLANE_CHOICES = [
    ("Flat on the bed, growing upwards", Plane.XY),
    ("Standing up, facing you", Plane.XZ),
    ("Standing up, edge on", Plane.YZ),
]

# A starting point beats an empty box. Each of these is a shape people actually
# begin with, and each is easier to edit than to type from nothing.
PRESETS: dict[str, tuple[tuple[float, float], ...]] = {
    "Rectangle": ((0, 0), (60, 0), (60, 40), (0, 40)),
    "L-bracket": ((0, 0), (60, 0), (60, 20), (20, 20), (20, 50), (0, 50)),
    "Triangle": ((0, 0), (50, 0), (25, 40)),
    "Hexagon": ((0, 17), (10, 0), (30, 0), (40, 17), (30, 34), (10, 34)),
    "Rounded tab": ((0, 0), (40, 0), (40, 20), (34, 26), (6, 26), (0, 20)),
}


# Profiles for spinning, as radius-from-the-axis and height. Each is a thing
# somebody would actually print, and each is easier to edit than to invent.
SPIN_PRESETS: dict[str, tuple[tuple[float, float], ...]] = {
    "Cup": ((0, 0), (25, 0), (25, 60), (22, 60), (22, 3), (0, 3)),
    "Vase": ((0, 0), (20, 0), (20, 4), (34, 30), (30, 60), (16, 80), (13, 80), (17, 60), (0, 5)),
    "Knob": ((0, 0), (18, 0), (18, 10), (12, 16), (12, 22), (0, 22)),
    "Washer": ((6, 0), (14, 0), (14, 3), (6, 3)),
    "Funnel": ((0, 0), (6, 0), (6, 20), (40, 55), (40, 58), (3, 24), (3, 0)),
}


def describe_profile(corners: list[tuple[float, float]]) -> str:
    """A line about a profile that is going to be spun, not extruded."""
    if len(corners) < MIN_OUTLINE_POINTS:
        return (
            f"{len(corners)} of at least {MIN_OUTLINE_POINTS} corners - this encloses nothing yet."
        )
    if enclosed_area(corners) < MIN_AREA_MM2:
        return f"{len(corners)} corners, but they enclose no area - are they all in a line?"
    widest = max(radius for radius, _ in corners)
    heights = [height for _, height in corners]
    tall = max(heights) - min(heights)
    return f"{len(corners)} corners, {widest * 2:g} mm across and {tall:g} mm tall when spun."


def parse_outline(text: str) -> list[tuple[float, float]]:
    """Read corners from text, one per line.

    Forgiving on purpose: "10, 20" and "10 20" and "(10, 20)" all mean the same
    corner, because this is as likely to be pasted from a spreadsheet or a chat
    window as typed. A line that is not a pair of numbers is skipped rather than
    rejecting the whole outline, so one bad row does not lose the rest.
    """
    corners: list[tuple[float, float]] = []
    for raw in text.splitlines():
        line = raw.strip().strip("()[]").replace(";", ",")
        if not line or line.startswith("#"):
            continue
        parts = [piece for piece in line.replace(",", " ").split() if piece]
        if len(parts) != 2:
            continue
        try:
            corners.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return corners


def enclosed_area(corners: list[tuple[float, float]]) -> float:
    """The area an outline encloses, in square millimetres.

    The shoelace formula, absolute so it does not matter which way round the
    corners were listed. Shown to the user because an outline that encloses
    nothing looks perfectly fine as a list of numbers and fails only once it
    reaches the kernel, where the message says nothing useful.
    """
    if len(corners) < MIN_OUTLINE_POINTS:
        return 0.0
    total = 0.0
    for index, (x, y) in enumerate(corners):
        next_x, next_y = corners[(index + 1) % len(corners)]
        total += x * next_y - next_x * y
    return abs(total) / 2


def describe_outline(corners: list[tuple[float, float]]) -> str:
    """A line of plain English about an outline, for under the preview."""
    if len(corners) < MIN_OUTLINE_POINTS:
        return (
            f"{len(corners)} of at least {MIN_OUTLINE_POINTS} corners - this encloses nothing yet."
        )
    area = enclosed_area(corners)
    if area < MIN_AREA_MM2:
        return f"{len(corners)} corners, but they enclose no area - are they all in a line?"
    width = max(x for x, _ in corners) - min(x for x, _ in corners)
    depth = max(y for _, y in corners) - min(y for _, y in corners)
    return f"{len(corners)} corners, {width:g} by {depth:g} mm, enclosing {area / 100:.1f} sq cm."


class OutlinePreview(QWidget):
    """The outline as it will be built, scaled to fit.

    Not a drawing surface: it reads the corners and shows them. Drawing with a
    mouse needs snapping, dimensions and an undo stack of its own, and getting
    that half right is worse than not offering it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the preview."""
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._corners: list[tuple[float, float]] = []
        self._axis = False

    def show_outline(self, corners: list[tuple[float, float]]) -> None:
        """Display a new set of corners."""
        self._corners = corners
        self.update()

    def show_axis(self, showing: bool) -> None:
        """Draw the axis a profile will spin about, or stop drawing it.

        Worth the few lines: a profile drawn without knowing where the axis is
        produces a shape with a hole through the middle nobody asked for.
        """
        self._axis = showing
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the outline, fitted to the widget with a margin."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1F2328"))

        if len(self._corners) < 2:
            painter.setPen(QPen(QColor(_CORNER_COLOUR)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nothing to show yet")
            return

        points = self._fitted()
        if self._axis:
            painter.setPen(QPen(QColor(_CORNER_COLOUR), 1, Qt.PenStyle.DashLine))
            left = min(point.x() for point in points)
            painter.drawLine(QPointF(left, 0.0), QPointF(left, float(self.height())))

        painter.setPen(QPen(QColor(_OUTLINE_COLOUR), 2))
        painter.setBrush(QBrush(_FILL_COLOUR))
        painter.drawPolygon(QPolygonF(points))

        painter.setBrush(QBrush(QColor(_CORNER_COLOUR)))
        painter.setPen(Qt.PenStyle.NoPen)
        for point in points:
            painter.drawEllipse(point, 3, 3)

    def _fitted(self) -> list[QPointF]:
        """The corners mapped into the widget, keeping their proportions.

        Y is flipped: millimetres grow away from the front of the bed, screen
        pixels grow downwards, and a preview that mirrors the part is worse
        than no preview at all.
        """
        margin = 16.0
        xs = [x for x, _ in self._corners]
        ys = [y for _, y in self._corners]
        span_x = max(max(xs) - min(xs), 0.001)
        span_y = max(max(ys) - min(ys), 0.001)
        scale = min(
            (self.width() - 2 * margin) / span_x,
            (self.height() - 2 * margin) / span_y,
        )
        left = margin + (self.width() - 2 * margin - span_x * scale) / 2
        top = margin + (self.height() - 2 * margin - span_y * scale) / 2
        return [
            QPointF(left + (x - min(xs)) * scale, top + (max(ys) - y) * scale)
            for x, y in self._corners
        ]


class OutlineDialog(QDialog):
    """Ask for a profile, and what to do with it.

    One dialog for both operations because they take the same thing. An
    extrusion and a revolution are both a closed outline and a decision;
    splitting them would duplicate the corner box, the preview and every rule
    about what counts as a shape.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the dialog."""
        super().__init__(parent)
        self.setWindowTitle("Draw a profile")
        self.setMinimumWidth(460)

        self._form = form = QFormLayout(self)

        self._operation = QComboBox()
        for member in Operation:
            self._operation.addItem(member.value.capitalize())
        self._operation.currentIndexChanged.connect(self._switch_operation)
        form.addRow("Then", self._operation)

        self._preset = QComboBox()
        self._preset.currentIndexChanged.connect(self._use_preset)
        form.addRow("Shape", self._preset)

        self._corners = QPlainTextEdit()
        self._corners.setPlaceholderText("0, 0")
        self._corners.setMinimumHeight(120)
        self._corners.textChanged.connect(self._refresh)
        form.addRow("Corners", self._corners)

        self._preview = OutlinePreview()
        form.addRow(self._preview)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(_HINT_STYLE)
        form.addRow(self._summary)

        self._height = QDoubleSpinBox()
        self._height.setRange(0.1, 500.0)
        self._height.setValue(8.0)
        self._height.setSingleStep(1.0)
        self._height.setSuffix(" mm")
        form.addRow("Thickness", self._height)

        self._plane = QComboBox()
        for label, _ in _PLANE_CHOICES:
            self._plane.addItem(label)
        form.addRow("Facing", self._plane)

        self._degrees = QDoubleSpinBox()
        self._degrees.setRange(1.0, 360.0)
        self._degrees.setValue(360.0)
        self._degrees.setSingleStep(15.0)
        self._degrees.setSuffix(" degrees")
        form.addRow("Round by", self._degrees)

        self._cut = QCheckBox("Cut this shape out of the model instead of adding it")
        form.addRow(self._cut)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(_HINT_STYLE)
        form.addRow(self._hint)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        form.addRow(self._buttons)

        self._switch_operation()

    # -------------------------------------------------------------- answering

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        """The corners, as typed."""
        return tuple(parse_outline(self._corners.toPlainText()))

    @property
    def thickness(self) -> float:
        """How thick to make it, in millimetres.

        Not ``height`` or ``depth``: ``QPaintDevice.depth()`` and
        ``QWidget.height()`` both already exist and mean something else, and
        quietly replacing one is how a widget starts drawing wrong for reasons
        nobody can find.
        """
        return float(self._height.value())

    @property
    def plane(self) -> Plane:
        """Which way an extruded profile faces."""
        return _PLANE_CHOICES[self._plane.currentIndex()][1]

    @property
    def operation(self) -> Operation:
        """Whether to give the profile thickness or spin it."""
        return list(Operation)[self._operation.currentIndex()]

    @property
    def degrees(self) -> float:
        """How far round to spin it."""
        return float(self._degrees.value())

    @property
    def cut(self) -> bool:
        """Whether this removes material rather than adding it."""
        return self._cut.isChecked()

    # ---------------------------------------------------------------- editing

    def set_outline(self, corners: tuple[tuple[float, float], ...]) -> None:
        """Put a set of corners into the box, replacing what is there."""
        lines = [f"{x:g}, {y:g}" for x, y in corners]
        self._corners.setPlainText(chr(10).join(lines))

    def _use_preset(self, index: int) -> None:
        if index > 0:
            self.set_outline(self._presets()[self._preset.itemText(index)])

    def _presets(self) -> dict[str, tuple[tuple[float, float], ...]]:
        """The starting shapes that make sense for the chosen operation."""
        return PRESETS if self.operation is Operation.EXTRUDE else SPIN_PRESETS

    def _switch_operation(self) -> None:
        """Show the settings the chosen operation needs, and hide the rest."""
        spinning = self.operation is Operation.REVOLVE

        self._preset.blockSignals(True)
        self._preset.clear()
        self._preset.addItem("Start from...")
        for name in self._presets():
            self._preset.addItem(name)
        self._preset.blockSignals(False)

        self._form.setRowVisible(self._height, not spinning)
        self._form.setRowVisible(self._plane, not spinning)
        self._form.setRowVisible(self._degrees, spinning)
        self._preview.show_axis(spinning)

        self._cut.setText(
            "Cut the spun shape out of the model instead of adding it"
            if spinning
            else "Cut this shape out of the model instead of adding it"
        )
        self._hint.setText(
            "One corner per line: how far from the axis, then how high. The axis "
            "is the dashed line on the left. The profile closes itself, and the "
            "finished shape stands upright - rotate it afterwards to lay it down."
            if spinning
            else "One corner per line, in millimetres. The outline closes itself, "
            "so there is no need to repeat the first corner, and the finished "
            "shape is centred on the part wherever you drew it."
        )
        self._refresh()

    def _refresh(self) -> None:
        corners = list(self.points)
        self._preview.show_outline(corners)
        self._summary.setText(
            describe_profile(corners)
            if self.operation is Operation.REVOLVE
            else describe_outline(corners)
        )
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(enclosed_area(corners) >= MIN_AREA_MM2)
