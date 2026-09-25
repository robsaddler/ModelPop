"""How long the thing that is running has been running.

A message saying "Repairing the mesh..." tells you the app is alive. It does
not tell you whether to wait or to go and make tea, and a repair here ranges
from under a second to a minute and a half on a million triangles. Without a
number, "it took ages" is a complaint nobody can act on; with one it is a
measurement.

So this sits at the **right-hand end of the status bar**, added as a permanent
widget so the transient messages that come and go never paint over it. It ticks
while anything is working and freezes on the final figure when the work stops,
which means the last operation's cost is still there to read afterwards.

It counts the whole span the app is busy, not one job. Two view-models work
independently here - the feature tree and the mesh workspace - and either can be
running; what a person is waiting for is the moment everything goes quiet.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

__all__ = ["HowLong", "spoken_duration"]

# Often enough to look like a clock rather than a progress guess, seldom enough
# to cost nothing. A tenth of a second is also the precision it is shown to.
TICK_MILLISECONDS = 100

# Past this, tenths of a second are false precision and a bare count of seconds
# is hard to read. "1m 36s" is the shape a kettle is measured in.
MINUTES_ABOVE_SECONDS = 60.0


def spoken_duration(seconds: float) -> str:
    """A duration the way someone would say it out loud."""
    if seconds < 0.0:
        seconds = 0.0
    if seconds < MINUTES_ABOVE_SECONDS:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m {rest:02d}s"


class HowLong(QLabel):
    """A timer for the end of the status bar.

    Runs while the app is busy and holds the last figure once it is not, so
    "how long did that take?" is answerable after the fact and not only during.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build the label.

        Args:
            parent: the usual Qt parent.
            clock: a monotonic source of seconds, so tests can drive time
                rather than sleep through it. Monotonic and not the wall
                clock: a duration must not change because the machine's time
                did.
        """
        super().__init__(parent)
        self._clock = clock
        self._started: float | None = None
        self._doing = ""
        self._elapsed = 0.0

        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Enough room for "1m 36s" so the status bar does not shuffle sideways
        # every time the figure gains a digit.
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance("00m 00s") + 8)
        self.setToolTip("How long the last thing took.")

        self._ticker = QTimer(self)
        self._ticker.setInterval(TICK_MILLISECONDS)
        self._ticker.timeout.connect(self._show_the_time)
        self._show_the_time()

    @property
    def seconds(self) -> float:
        """The figure on show, running or finished."""
        if self._started is not None:
            return max(0.0, self._clock() - self._started)
        return self._elapsed

    @property
    def running(self) -> bool:
        """Whether it is still counting."""
        return self._started is not None

    def busy(self, busy: bool, doing: str = "") -> None:
        """Follow the app between working and idle.

        Called with the *combined* state of every view-model, so one job
        finishing while another runs does not stop the clock or restart it.
        A second call saying the same thing is ignored.
        """
        if doing:
            self._doing = doing
        if busy:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        if self._started is not None:
            return
        self._started = self._clock()
        self._elapsed = 0.0
        self._ticker.start()
        self._show_the_time()

    def _stop(self) -> None:
        if self._started is None:
            return
        self._elapsed = max(0.0, self._clock() - self._started)
        self._started = None
        self._ticker.stop()
        self._show_the_time()

    def _show_the_time(self) -> None:
        spoken = spoken_duration(self.seconds)
        self.setText(spoken)
        if self._started is not None:
            self.setToolTip(f"{self._doing or 'Working'} - {spoken} so far.")
        elif self._doing:
            self.setToolTip(f"{self._doing} took {spoken}.")
        else:
            self.setToolTip("How long the last thing took.")
