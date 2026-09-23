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

import math
from enum import Enum
from itertools import pairwise
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

from modelpop.domain.cad_commands import MIN_OUTLINE_POINTS, MIN_PATH_POINTS, Plane

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

__all__ = [
    "LOFT_PRESETS",
    "SPIN_PRESETS",
    "SWEEP_PRESETS",
    "Operation",
    "OutlineDialog",
    "OutlinePreview",
    "describe_outline",
    "describe_path",
    "describe_profile",
    "describe_sections",
    "enclosed_area",
    "flatten_path",
    "parse_outline",
    "parse_path",
    "parse_sections",
    "path_length",
]


class Operation(Enum):
    """What to do with a profile once it is drawn."""

    EXTRUDE = "give it thickness"
    REVOLVE = "spin it round"
    SWEEP = "push it along a path"
    LOFT = "blend it into another"


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


# A sweep needs two drawings, not one, so its presets carry both: the
# cross-section and the path it travels along. Offering one without the other
# leaves the user typing the harder half from nothing.
SWEEP_PRESETS: dict[
    str,
    tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float, float], ...]],
] = {
    "Grab handle": (
        ((-4, -4), (4, -4), (4, 4), (-4, 4)),
        ((-30, 0, 0), (-30, 0, 28), (30, 0, 28), (30, 0, 0)),
    ),
    "Cable channel": (
        ((-6, 0), (6, 0), (6, 8), (4, 8), (4, 2), (-4, 2), (-4, 8), (-6, 8)),
        ((-40, 0, 0), (40, 0, 0)),
    ),
    "Bent tube": (
        ((-5, -5), (5, -5), (5, 5), (-5, 5)),
        ((0, 0, 0), (0, 0, 40), (35, 0, 40)),
    ),
    "Skirting trim": (
        ((0, 0), (10, 0), (10, 14), (7, 20), (0, 20)),
        ((0, 0, 0), (60, 0, 0), (60, 45, 0)),
    ),
    "Staple": (
        ((-2, -2), (2, -2), (2, 2), (-2, 2)),
        ((-15, 0, 0), (-15, 0, 20), (15, 0, 20), (15, 0, 0)),
    ),
}


# Stacked outlines, each with the height it sits at. These are the shapes a
# changing cross-section is actually for, and all of them are painful to
# describe any other way.
LOFT_PRESETS: dict[str, tuple[tuple[tuple[tuple[float, float], ...], float], ...]] = {
    "Tapered pot": (
        (((-18, -18), (18, -18), (18, 18), (-18, 18)), 0.0),
        (((-30, -30), (30, -30), (30, 30), (-30, 30)), 55.0),
    ),
    "Funnel": (
        (((-8, -8), (8, -8), (8, 8), (-8, 8)), 0.0),
        (((-10, -10), (10, -10), (10, 10), (-10, 10)), 20.0),
        (((-35, -35), (35, -35), (35, 35), (-35, 35)), 50.0),
    ),
    "Square to triangle": (
        (((-20, -20), (20, -20), (20, 20), (-20, 20)), 0.0),
        (((-16, -12), (16, -12), (0, 16)), 30.0),
    ),
    "Wedge": (
        (((-25, -25), (25, -25), (25, 25), (-25, 25)), 0.0),
        (((-25, -4), (25, -4), (25, 4), (-25, 4)), 40.0),
    ),
    "Bulged vase": (
        (((-14, -14), (14, -14), (14, 14), (-14, 14)), 0.0),
        (((-26, -26), (26, -26), (26, 26), (-26, 26)), 30.0),
        (((-11, -11), (11, -11), (11, 11), (-11, 11)), 70.0),
    ),
}


# What the checkbox says, per operation. Written out rather than assembled from
# fragments: "Cut the blended shape out of the model" reads as English and
# "Cut the loft out" does not.
_CUT_LABELS: dict[Operation, str] = {
    Operation.EXTRUDE: "Cut this shape out of the model instead of adding it",
    Operation.REVOLVE: "Cut the spun shape out of the model instead of adding it",
    Operation.SWEEP: "Cut the swept shape out of the model instead of adding it",
    Operation.LOFT: "Cut the blended shape out of the model instead of adding it",
}


# The same numbers mean four different things, so each operation says which.
# Inferring it from a column heading is how somebody types a radius into an x.
_HINTS: dict[Operation, str] = {
    Operation.EXTRUDE: (
        "One corner per line, in millimetres. The outline closes itself, so "
        "there is no need to repeat the first corner, and the finished shape "
        "is centred on the part wherever you drew it."
    ),
    Operation.REVOLVE: (
        "One corner per line: how far from the axis, then how high. The axis "
        "is the dashed line on the left. The profile closes itself, and the "
        "finished shape stands upright - rotate it afterwards to lay it down."
    ),
    Operation.SWEEP: (
        "The corners are the cross-section, drawn around its own centre. The "
        "path is where that cross-section travels, three numbers a line. It is "
        "placed square to the start of the path, so there is no plane to pick, "
        "and the corners of the path are rounded by the bend radius - a mitred "
        "corner folds through itself and is not a shape."
    ),
    Operation.LOFT: (
        "Three numbers a line: the corner, then the height its outline sits "
        "at. Every line sharing a height belongs to the same outline, and the "
        "shape blends from one to the next. Each outline keeps where it was "
        "drawn, so a stack that is off to one side leans."
    ),
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


def parse_path(text: str) -> list[tuple[float, float, float]]:
    """Read a three-dimensional path from text, one point per line.

    As forgiving as ``parse_outline`` and for the same reasons, but insisting
    on three numbers. A path is where the outline *travels*, so a two-number
    line is an outline corner in the wrong box rather than a point with an
    implied height, and guessing which axis was meant would be worse than
    skipping it.
    """
    points: list[tuple[float, float, float]] = []
    for raw in text.splitlines():
        line = raw.strip().strip("()[]").replace(";", ",")
        if not line or line.startswith("#"):
            continue
        parts = [piece for piece in line.replace(",", " ").split() if piece]
        if len(parts) != 3:
            continue
        try:
            points.append((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    return points


def parse_sections(text: str) -> list[tuple[list[tuple[float, float]], float]]:
    """Read stacked outlines from text, grouped by the height on each line.

    Three numbers a line - x, y and the height that corner's outline sits at -
    and every line sharing a height belongs to the same outline. Grouping by
    the number rather than by blank lines is deliberate: a blank line is
    invisible, and an outline that silently split in two because of one is a
    shape nobody could debug by looking at it.

    Heights keep the order they first appear in, so the text reads the way it
    was typed. The command sorts them afterwards.
    """
    grouped: dict[float, list[tuple[float, float]]] = {}
    for x, y, height in parse_path(text):
        grouped.setdefault(height, []).append((x, y))
    return [(corners, height) for height, corners in grouped.items()]


def path_length(points: list[tuple[float, float, float]]) -> float:
    """How far a path travels, corner to corner, in millimetres."""
    return sum(math.dist(start, end) for start, end in pairwise(points))


def describe_path(points: list[tuple[float, float, float]]) -> str:
    """A line of plain English about a path, for under its preview."""
    if len(points) < MIN_PATH_POINTS:
        return f"{len(points)} of at least {MIN_PATH_POINTS} points - this goes nowhere yet."
    corners = len(points) - 2
    bends = "no corners" if corners <= 0 else f"{corners} corner{'s' if corners > 1 else ''}"
    return f"{len(points)} points, {path_length(points):g} mm long, with {bends}."


def describe_sections(sections: list[tuple[list[tuple[float, float]], float]]) -> str:
    """A line of plain English about a stack of outlines."""
    if len(sections) < 2:
        return f"{len(sections)} of at least 2 outlines - there is nothing to blend between yet."

    thin = [f"{height:g} mm" for corners, height in sections if len(corners) < MIN_OUTLINE_POINTS]
    if thin:
        return f"The outline at {thin[0]} has fewer than {MIN_OUTLINE_POINTS} corners."

    heights = sorted(height for _, height in sections)
    repeated = [f"{lower:g} mm" for lower, upper in pairwise(heights) if upper == lower]
    if repeated:
        return f"Two outlines are both at {repeated[0]}. Give each one its own height."

    return (
        f"{len(sections)} outlines over {heights[-1] - heights[0]:g} mm, "
        f"from {heights[0]:g} to {heights[-1]:g} mm."
    )


def flatten_path(points: list[tuple[float, float, float]]) -> tuple[list[tuple[float, float]], str]:
    """A path projected onto the plane it mostly lies in, and what to call it.

    A path typed blind is easy to get wrong, and a preview of it is worth more
    than the few lines it costs. The axis with the least spread is the one
    dropped, because that is the direction the path barely uses and therefore
    the one worth looking along.
    """
    if not points:
        return [], "from the front"

    spans = [max(axis) - min(axis) for axis in zip(*points, strict=True)]
    dropped = spans.index(min(spans))
    views = {0: (1, 2, "from the side"), 1: (0, 2, "from the front"), 2: (0, 1, "from above")}
    across, up, label = views[dropped]
    return [(point[across], point[up]) for point in points], label


class OutlinePreview(QWidget):
    """The outline as it will be built, scaled to fit.

    Not a drawing surface: it reads the corners and shows them. Drawing with a
    mouse needs snapping, dimensions and an undo stack of its own, and getting
    that half right is worse than not offering it.

    It shows *several* outlines together for a blend, because the whole
    question there is how the cross-section changes on the way up, and one
    section at a time answers none of it. All of them share one scale, so a
    section that is twice the size looks twice the size.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the preview."""
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._outlines: list[list[tuple[float, float]]] = []
        self._axis = False
        self._closed = True

    def show_outline(self, corners: list[tuple[float, float]]) -> None:
        """Display a single set of corners."""
        self.show_outlines([corners] if corners else [])

    def show_outlines(self, outlines: list[list[tuple[float, float]]]) -> None:
        """Display several sets of corners on one scale."""
        self._outlines = [corners for corners in outlines if corners]
        self.update()

    def show_axis(self, showing: bool) -> None:
        """Draw the axis a profile will spin about, or stop drawing it.

        Worth the few lines: a profile drawn without knowing where the axis is
        produces a shape with a hole through the middle nobody asked for.
        """
        self._axis = showing
        self.update()

    def show_closed(self, closed: bool) -> None:
        """Whether these corners join up.

        A path does not. Drawing one closed would show a loop back to the start
        that is not in the shape, which is exactly the kind of preview that is
        worse than none.
        """
        self._closed = closed
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw every outline, fitted to the widget with a margin."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1F2328"))

        if not any(len(corners) >= 2 for corners in self._outlines):
            painter.setPen(QPen(QColor(_CORNER_COLOUR)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nothing to show yet")
            return

        drawn = [self._fitted(corners) for corners in self._outlines]

        if self._axis:
            painter.setPen(QPen(QColor(_CORNER_COLOUR), 1, Qt.PenStyle.DashLine))
            left = min(point.x() for points in drawn for point in points)
            painter.drawLine(QPointF(left, 0.0), QPointF(left, float(self.height())))

        for points in drawn:
            painter.setPen(QPen(QColor(_OUTLINE_COLOUR), 2))
            if self._closed:
                painter.setBrush(QBrush(_FILL_COLOUR))
                painter.drawPolygon(QPolygonF(points))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(QPolygonF(points))

            painter.setBrush(QBrush(QColor(_CORNER_COLOUR)))
            painter.setPen(Qt.PenStyle.NoPen)
            for point in points:
                painter.drawEllipse(point, 3, 3)

    def _fitted(self, corners: list[tuple[float, float]]) -> list[QPointF]:
        """One outline mapped into the widget, on the scale shared by them all.

        Y is flipped: millimetres grow away from the front of the bed, screen
        pixels grow downwards, and a preview that mirrors the part is worse
        than no preview at all.
        """
        margin = 16.0
        every = [point for outline in self._outlines for point in outline]
        xs = [x for x, _ in every]
        ys = [y for _, y in every]
        span_x = max(max(xs) - min(xs), 0.001)
        span_y = max(max(ys) - min(ys), 0.001)
        scale = min(
            (self.width() - 2 * margin) / span_x,
            (self.height() - 2 * margin) / span_y,
        )
        left = margin + (self.width() - 2 * margin - span_x * scale) / 2
        top = margin + (self.height() - 2 * margin - span_y * scale) / 2
        return [
            QPointF(left + (x - min(xs)) * scale, top + (max(ys) - y) * scale) for x, y in corners
        ]


class OutlineDialog(QDialog):
    """Ask for a profile, and what to do with it.

    One dialog for all four operations because they take the same thing. An
    extrusion, a revolution, a sweep and a blend all start from a closed
    outline and a decision; splitting them would duplicate the corner box, the
    preview and every rule about what counts as a shape.

    What changes between them is what the numbers *mean*, so the hint under the
    box says so in each case rather than leaving the user to infer it from a
    label. Two of the four need a second drawing - a path to travel along, or
    the height each outline sits at - and those rows appear only when they are
    the ones being asked for.
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

        self._path = QPlainTextEdit()
        self._path.setPlaceholderText("0, 0, 0")
        self._path.setMinimumHeight(90)
        self._path.textChanged.connect(self._refresh)
        form.addRow("Path", self._path)

        self._path_preview = OutlinePreview()
        self._path_preview.setMinimumHeight(120)
        self._path_preview.show_closed(False)
        form.addRow(self._path_preview)

        self._path_summary = QLabel()
        self._path_summary.setWordWrap(True)
        self._path_summary.setStyleSheet(_HINT_STYLE)
        form.addRow(self._path_summary)

        self._bend = QDoubleSpinBox()
        self._bend.setRange(0.1, 200.0)
        self._bend.setValue(3.0)
        self._bend.setSingleStep(0.5)
        self._bend.setSuffix(" mm")
        self._bend.setToolTip(
            "How much the corners of the path are rounded. A mitred corner is "
            "not a sharp corner - it is a solid folded through itself - so "
            "there is always some bend."
        )
        form.addRow("Bend", self._bend)

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
    def path(self) -> tuple[tuple[float, float, float], ...]:
        """The path a swept outline travels along, as typed."""
        return tuple(parse_path(self._path.toPlainText()))

    @property
    def sections(self) -> tuple[tuple[tuple[tuple[float, float], ...], float], ...]:
        """The stacked outlines a blend runs through, as typed.

        Read from the same box as the corners, because they *are* corners -
        each one carrying the height its outline sits at.
        """
        return tuple(
            (tuple(corners), height)
            for corners, height in parse_sections(self._corners.toPlainText())
        )

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
        """What to do with the profile once it is drawn."""
        return list(Operation)[self._operation.currentIndex()]

    @property
    def degrees(self) -> float:
        """How far round to spin it."""
        return float(self._degrees.value())

    @property
    def bend_radius(self) -> float:
        """How much to round the corners of a swept path."""
        return float(self._bend.value())

    @property
    def cut(self) -> bool:
        """Whether this removes material rather than adding it."""
        return self._cut.isChecked()

    # ---------------------------------------------------------------- editing

    def set_outline(self, corners: tuple[tuple[float, float], ...]) -> None:
        """Put a set of corners into the box, replacing what is there."""
        lines = [f"{x:g}, {y:g}" for x, y in corners]
        self._corners.setPlainText(chr(10).join(lines))

    def set_path(self, points: tuple[tuple[float, float, float], ...]) -> None:
        """Put a path into the path box, replacing what is there."""
        lines = [f"{x:g}, {y:g}, {z:g}" for x, y, z in points]
        self._path.setPlainText(chr(10).join(lines))

    def set_sections(
        self, sections: tuple[tuple[tuple[tuple[float, float], ...], float], ...]
    ) -> None:
        """Put a stack of outlines into the corners box, one corner per line."""
        lines = [f"{x:g}, {y:g}, {height:g}" for corners, height in sections for x, y in corners]
        self._corners.setPlainText(chr(10).join(lines))

    def _use_preset(self, index: int) -> None:
        if index <= 0:
            return
        name = self._preset.itemText(index)
        if self.operation is Operation.SWEEP:
            section, route = SWEEP_PRESETS[name]
            self.set_outline(section)
            self.set_path(route)
        elif self.operation is Operation.LOFT:
            self.set_sections(LOFT_PRESETS[name])
        else:
            self.set_outline(self._presets()[name])

    def _presets(self) -> dict[str, tuple[tuple[float, float], ...]]:
        """The starting shapes that make sense for the chosen operation."""
        return PRESETS if self.operation is Operation.EXTRUDE else SPIN_PRESETS

    def _preset_names(self) -> list[str]:
        """What to offer in the "start from" list for the chosen operation."""
        if self.operation is Operation.SWEEP:
            return list(SWEEP_PRESETS)
        if self.operation is Operation.LOFT:
            return list(LOFT_PRESETS)
        return list(self._presets())

    def _switch_operation(self) -> None:
        """Show the settings the chosen operation needs, and hide the rest."""
        operation = self.operation
        spinning = operation is Operation.REVOLVE
        sweeping = operation is Operation.SWEEP
        blending = operation is Operation.LOFT

        self._preset.blockSignals(True)
        self._preset.clear()
        self._preset.addItem("Start from...")
        for name in self._preset_names():
            self._preset.addItem(name)
        self._preset.blockSignals(False)

        self._form.setRowVisible(self._height, operation is Operation.EXTRUDE)
        self._form.setRowVisible(self._plane, operation is Operation.EXTRUDE)
        self._form.setRowVisible(self._degrees, spinning)
        self._form.setRowVisible(self._path, sweeping)
        self._form.setRowVisible(self._path_preview, sweeping)
        self._form.setRowVisible(self._path_summary, sweeping)
        self._form.setRowVisible(self._bend, sweeping)
        self._preview.show_axis(spinning)

        self._corners.setPlaceholderText("0, 0, 0" if blending else "0, 0")
        self._cut.setText(_CUT_LABELS[operation])
        self._hint.setText(_HINTS[operation])
        self._refresh()

    def _refresh(self) -> None:
        operation = self.operation
        if operation is Operation.LOFT:
            self._refresh_blend()
        else:
            self._refresh_outline(operation)

        if operation is Operation.SWEEP:
            route = list(self.path)
            flattened, view = flatten_path(route)
            self._path_preview.show_outline(flattened)
            seen = f" Seen {view}." if len(route) >= MIN_PATH_POINTS else ""
            self._path_summary.setText(describe_path(route) + seen)

        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self._is_buildable())

    def _refresh_outline(self, operation: Operation) -> None:
        """Show one outline, described as the chosen operation reads it."""
        corners = list(self.points)
        self._preview.show_outlines([corners])
        self._summary.setText(
            describe_profile(corners)
            if operation is Operation.REVOLVE
            else describe_outline(corners)
        )

    def _refresh_blend(self) -> None:
        """Show every outline in the stack, on one scale."""
        sections = self.sections
        self._preview.show_outlines([list(corners) for corners, _ in sections])
        self._summary.setText(
            describe_sections([(list(corners), height) for corners, height in sections])
        )

    def _is_buildable(self) -> bool:
        """Whether what has been typed so far could actually be built.

        The same rules the view-model enforces, asked early so the button is
        simply unavailable rather than clickable and then refused. Anything
        subtler than this - a path too tight for its bend, a self-intersecting
        outline - is the view-model's to report, because it needs the command
        to work it out.
        """
        operation = self.operation
        if operation is Operation.LOFT:
            sections = self.sections
            return (
                len(sections) >= 2
                and all(enclosed_area(list(corners)) >= MIN_AREA_MM2 for corners, _ in sections)
                and len({height for _, height in sections}) == len(sections)
            )

        if enclosed_area(list(self.points)) < MIN_AREA_MM2:
            return False
        if operation is Operation.SWEEP:
            return len(self.path) >= MIN_PATH_POINTS
        return True
