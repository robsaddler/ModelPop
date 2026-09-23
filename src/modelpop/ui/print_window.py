"""Watch the print happen.

Rob asked for this directly: configure the printer, simulate the head moving the
way the machine will move it, and see the thing built. It is also the single
most educational view the app has, because it turns "why did that fail" from a
mystery into something you can scrub back to and look at.

The window owns no logic beyond redrawing. Where the head is, how long the print
takes and which clock that came from are all ``VirtualPrint``; how to draw it is
``modelpop.rendering.print_view``. This file wires a slider to those two.

**Redraw cost is why the toolpath is rebuilt on a timer rather than per frame.**
Dragging a slider fires dozens of events a second, and rebuilding a few hundred
thousand lines that often makes the drag feel broken. Coalescing them into one
redraw every few tens of milliseconds keeps it smooth without dropping the end
state, because the last event always wins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor

from modelpop.domain.printer import PrinterProfile
from modelpop.printing.simulate import VirtualPrint
from modelpop.rendering.print_view import (
    NOZZLE_COLOUR,
    nozzle_marker,
    progress_of,
    to_lines,
)
from modelpop.rendering.viewport import ViewportScene

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PrintWindow"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"

# How finely the slider divides the print. A thousand steps is finer than anyone
# can drag and coarse enough that the value stays an integer.
_STEPS = 1000

# Coalesce slider events into one redraw. Fast enough to feel live, slow enough
# that a drag does not queue a hundred rebuilds of the whole toolpath.
_REDRAW_MS = 40

# Real time per tick when playing. The print is sped up so a twelve-minute job
# takes about twenty seconds to watch, which is the point of watching it.
_PLAY_MS = 33
_SPEEDS = (30.0, 120.0, 600.0)


class PrintWindow(QDialog):
    """A scrubable view of a sliced print."""

    def __init__(
        self,
        gcode: Path,
        printer: PrinterProfile | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Load a G-code file and show it.

        Args:
            gcode: the sliced file to play.
            printer: the machine, for drawing the build volume.
            parent: the owning window.
        """
        super().__init__(parent)
        self._play = VirtualPrint.read(gcode)
        self._printer = printer or PrinterProfile.p2s()
        self._speed = _SPEEDS[0]
        self._drawn_upto = -1
        self._toolpath_actor: Any = None
        self._nozzle_actor: Any = None

        self.setWindowTitle("Watch it print")
        self.setMinimumSize(900, 680)
        self._build()

        if self._play.can_play:
            self._scene.frame_model()
            self._seek(0)

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        self._plotter = QtInteractor(self)
        self._scene = ViewportScene(self._plotter, self._printer)
        layout.addWidget(self._plotter.interactor, stretch=1)

        self._caption = QLabel(self._play.describe())
        self._caption.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._caption)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, _STEPS)
        self._slider.setEnabled(self._play.can_play)
        self._slider.valueChanged.connect(self._slider_moved)
        layout.addWidget(self._slider)

        layout.addLayout(self._build_controls())

        # One timer coalesces slider events; another advances playback.
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(_REDRAW_MS)
        self._redraw.timeout.connect(self._draw)

        self._ticker = QTimer(self)
        self._ticker.setInterval(_PLAY_MS)
        self._ticker.timeout.connect(self._advance)

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self._play_button = QPushButton("Play")
        self._play_button.setEnabled(self._play.can_play)
        self._play_button.clicked.connect(self._toggle_play)
        row.addWidget(self._play_button)

        self._speed_button = QPushButton(f"{int(self._speed)}x")
        self._speed_button.setToolTip("How much faster than real time")
        self._speed_button.clicked.connect(self._cycle_speed)
        row.addWidget(self._speed_button)

        restart = QPushButton("Back to the start")
        restart.clicked.connect(lambda: self._seek(0))
        row.addWidget(restart)

        self._show_nozzle = QCheckBox("Show the nozzle")
        self._show_nozzle.setChecked(True)
        self._show_nozzle.toggled.connect(self._draw)
        row.addWidget(self._show_nozzle)

        row.addStretch(1)

        self._progress = QLabel()
        self._progress.setStyleSheet(_HINT_STYLE)
        row.addWidget(self._progress)
        return row

    # --------------------------------------------------------------- controls

    def _toggle_play(self) -> None:
        if self._ticker.isActive():
            self._ticker.stop()
            self._play_button.setText("Play")
            return
        if self._slider.value() >= _STEPS:
            self._seek(0)
        self._ticker.start()
        self._play_button.setText("Pause")

    def _cycle_speed(self) -> None:
        index = (_SPEEDS.index(self._speed) + 1) % len(_SPEEDS)
        self._speed = _SPEEDS[index]
        self._speed_button.setText(f"{int(self._speed)}x")

    def _advance(self) -> None:
        if not self._play.can_play:
            self._ticker.stop()
            return
        step = self._speed * (_PLAY_MS / 1000.0) / self._play.total_seconds * _STEPS
        nxt = self._slider.value() + max(1, int(step))
        if nxt >= _STEPS:
            self._seek(_STEPS)
            self._ticker.stop()
            self._play_button.setText("Play")
            return
        self._slider.setValue(nxt)

    def _seek(self, step: int) -> None:
        self._slider.setValue(step)
        self._draw()

    def _slider_moved(self, _: int) -> None:
        """Schedule a redraw rather than doing one per event."""
        self._progress.setText(progress_of(self._play, self._seconds()))
        self._redraw.start()

    def _seconds(self) -> float:
        if not self._play.can_play:
            return 0.0
        return self._play.total_seconds * (self._slider.value() / _STEPS)

    # ---------------------------------------------------------------- drawing

    def _draw(self) -> None:
        if not self._play.can_play:
            return

        moment = self._seconds()
        frame = self._play.at(moment)

        # Rebuilding only when the set of drawn moves actually changed. Dragging
        # inside one long move is common and redrawing for it is wasted work.
        if frame.segment != self._drawn_upto:
            self._drawn_upto = frame.segment
            self._forget(self._toolpath_actor)
            self._toolpath_actor = None

            laid = self._play.extruded_by(moment)
            if laid:
                self._toolpath_actor = self._plotter.add_mesh(
                    to_lines(laid),
                    name="toolpath",
                    scalars="feature",
                    rgb=True,
                    line_width=2,
                    reset_camera=False,
                    render=False,
                )

        self._forget(self._nozzle_actor)
        self._nozzle_actor = None
        if self._show_nozzle.isChecked():
            self._nozzle_actor = self._plotter.add_mesh(
                nozzle_marker(frame),
                name="nozzle",
                color=NOZZLE_COLOUR,
                reset_camera=False,
                render=False,
            )

        self._plotter.render()
        self._progress.setText(progress_of(self._play, moment))

    def _forget(self, actor: Any) -> None:
        """Take an actor out of the scene, if there is one."""
        if actor is not None:
            self._plotter.remove_actor(actor, reset_camera=False, render=False)

    def closeEvent(self, event: object) -> None:  # noqa: N802 - Qt's spelling
        """Stop the timers and release the render window."""
        self._ticker.stop()
        self._redraw.stop()
        self._plotter.close()
        super().closeEvent(event)  # type: ignore[arg-type]
