"""Doing work somewhere other than the interface thread.

The rule this exists to enforce is not subtle: **nothing but interface work
runs on the interface thread**. Everything slow in this application is a
subprocess or a socket - a CAD rebuild, a slice, a generation, a
reconstruction, a job sent to a printer - and every one of them would freeze
the window for as long as it took.

It was written for CAD rebuilds and named after them, while the workspace kept
running inline "because Phase 1 operations are fast enough". They were. Then
slicing, generation, photogrammetry and printer I/O were built behind the same
view-model and nobody revisited the comment, so a minute-long generation ran on
the interface thread. Hence the neutral name: this is for anything slow, and
there is no second way of doing it.

Work started here announces from a worker thread. Whatever it announces *to*
must cross back through a Qt signal before touching a widget or VTK - see
``_WindowSignals`` in the main window, and the trap it is named after.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtWidgets import QWidget

__all__ = ["BackgroundJob", "BackgroundRunner"]


class BackgroundJob(QObject):
    """Runs one piece of work on a thread and says when it is finished."""

    done = Signal()

    def __init__(self, work: Callable[[], None]) -> None:
        super().__init__()
        self._work = work

    def run(self) -> None:
        """Do the work, and report finishing even if it raised."""
        try:
            self._work()
        finally:
            self.done.emit()


class BackgroundRunner:
    """Keeps the window responsive while OCCT works.

    A rebuild is a subprocess taking a second or two. Running it on the
    interface thread freezes the window for exactly as long, which reads as a
    crash.

    Both the thread **and** the worker are held for as long as the work lasts.
    Keeping only the thread is the obvious version and it silently does nothing:
    the worker has no parent, so it is collected the moment this returns, and
    the queued ``started`` connection dies with it. The thread then starts, runs
    an empty event loop, and waits forever. No exception, no output, no clue.
    This was written that way first and cost an hour.
    """

    def __init__(self, owner: QWidget) -> None:
        """Hold threads and their workers for as long as they run."""
        self._owner = owner
        self._live: list[tuple[QThread, BackgroundJob]] = []

    def __call__(self, work: Callable[[], None]) -> None:
        """Start one piece of work on its own thread."""
        thread = QThread(self._owner)
        worker = BackgroundJob(work)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: self._forget(thread))

        self._live.append((thread, worker))
        thread.start()

    def _forget(self, thread: QThread) -> None:
        self._live = [pair for pair in self._live if pair[0] is not thread]

    @property
    def running(self) -> int:
        """How much work is in flight. For tests, and for diagnostics."""
        return len(self._live)
