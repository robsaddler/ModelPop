"""The panel that watches a print.

A tool window rather than a modal: watching a print is something you leave open
beside the model while you carry on doing something else, and a dialog that
blocked the application for the length of a print would be absurd.

It owns a clock and nothing else. Every decision about how often to ask, what to
do when the answer does not arrive, and when to stop asking belongs to
``PrinterMonitor``, which has no timer in it and is therefore testable in
milliseconds rather than in hours.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modelpop.presentation.monitoring import PrinterMonitor

if TYPE_CHECKING:
    from collections.abc import Callable

    from modelpop.application.printer_ports import PrinterStatus
    from modelpop.domain.printer import PrinterConnection
    from modelpop.domain.result import Result
    from modelpop.presentation.monitoring import Watch

__all__ = ["MonitorDialog"]

_HINT_STYLE = "color: #9AA5B1; font-size: 11px;"


class _Asking(QThread):
    """One question to the printer, off the interface thread.

    A printer that is switched off costs the whole connection timeout, and a
    window frozen for that long is how a user concludes the application has
    crashed. The thread is held by the dialog, not just started: a worker with
    no reference left is collected, and the thread then runs an empty event
    loop forever with no error anywhere.
    """

    def __init__(self, monitor: PrinterMonitor, parent: QWidget | None = None) -> None:
        """Wire the thread to the monitor it will poll."""
        super().__init__(parent)
        self._monitor = monitor

    def run(self) -> None:
        """Ask once.

        The answer is not carried back from here: the monitor announces it
        itself, and the dialog has already arranged for that to arrive through
        a signal. A second route would only draw everything twice.
        """
        self._monitor.poll()


class MonitorDialog(QDialog):
    """What the printer is doing, refreshed while it does it.

    **Every reading crosses back through ``reading`` before it touches a
    widget.** ``PrinterMonitor.poll`` runs on the worker and announces inline
    from there, so a listener registered as a bound method is a widget being
    driven from the wrong thread. This window did exactly that and took the
    application down mid-print - and only mid-print, because idle polls leave
    the progress bar hidden and a hidden bar asks for no repaint. Once the
    print was running, every poll called ``setValue`` on a visible bar from a
    thread with no business doing it.
    """

    reading = Signal(object)
    """One ``Watch``, carried from whichever thread produced it."""

    def __init__(
        self,
        ask: Callable[[PrinterConnection], Result[PrinterStatus]],
        connection: PrinterConnection,
        parent: QWidget | None = None,
    ) -> None:
        """Build the panel around a way of asking and a printer to ask."""
        super().__init__(parent)
        self._monitor = PrinterMonitor(ask, connection)
        self._asking: _Asking | None = None

        self.setWindowTitle("Watching the print")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        self._headline = QLabel("Asking the printer...")
        self._headline.setWordWrap(True)
        self._headline.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self._headline)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(True)
        layout.addWidget(self._progress)

        self._temperatures = QLabel()
        self._temperatures.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._temperatures)

        row = QHBoxLayout()
        row.addStretch(1)
        self._stop = QPushButton("Stop watching")
        self._stop.clicked.connect(self._stop_watching)
        row.addWidget(self._stop)
        layout.addLayout(row)

        note = QLabel(
            "This only reads. Nothing here pauses or cancels the print - that is "
            "the printer's own screen, which is where you would be standing if "
            "something had gone wrong."
        )
        note.setWordWrap(True)
        note.setStyleSheet(_HINT_STYLE)
        layout.addWidget(note)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._ask)

        # Through the signal, never as a bound method. This is the rule in
        # CLAUDE.md trap 7 and this window is the third place to break it.
        self.reading.connect(self._show)
        self._monitor.on_change(self.reading.emit)
        self._ask()

    # ------------------------------------------------------------------ input

    def _ask(self) -> None:
        """Put one question to the printer, off the interface thread."""
        if not self._monitor.is_watching or self._asking is not None:
            return

        self._asking = _Asking(self._monitor, self)
        self._asking.finished.connect(self._done_asking)
        self._asking.start()

    def _done_asking(self) -> None:
        self._asking = None
        wait = self._monitor.watch.next_interval_seconds
        if self._monitor.is_watching and wait > 0:
            self._timer.start(int(wait * 1000))

    def _stop_watching(self) -> None:
        self._timer.stop()
        self._monitor.stop("Stopped watching.")
        self._show(self._monitor.watch)

    def closeEvent(self, event: object) -> None:  # noqa: N802
        """Stop the clock and let the outstanding question finish.

        Without the wait, the thread outlives the widgets its signal is
        connected to, which is a crash rather than a tidy exit.
        """
        self._timer.stop()
        self._monitor.stop("Stopped watching.")
        if self._asking is not None:
            self._asking.wait(5000)
        super().closeEvent(event)  # type: ignore[arg-type]

    # ----------------------------------------------------------------- output

    def _show(self, watch: Watch) -> None:
        """Draw whatever the monitor now knows."""
        self._headline.setText(watch.describe())

        status = watch.status
        busy = status.state.is_busy
        self._progress.setVisible(busy)
        if busy:
            self._progress.setValue(int(status.percent_done))

        if status.nozzle_celsius or status.bed_celsius:
            self._temperatures.setText(
                f"Nozzle {status.nozzle_celsius:.0f} C, bed {status.bed_celsius:.0f} C"
            )
        else:
            self._temperatures.setText("")

        self._stop.setEnabled(watch.watching)
