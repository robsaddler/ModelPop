"""Watching a print while it runs.

The last thing anyone does with a printer: send the job, then keep half an eye
on it. The gateway already answers "what are you doing"; this decides how often
to ask, what to do when the answer does not arrive, and when to stop asking.

No timer in here and no UI framework. The interface owns a clock and calls
``poll`` when it fires, which is the only reason a watch that backs off after
failures, gives up after enough of them, and stops of its own accord when the
print finishes is testable in milliseconds rather than in hours.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from modelpop.application.printer_ports import PrinterState, PrinterStatus
from modelpop.domain.result import Failure

if TYPE_CHECKING:
    from modelpop.domain.printer import PrinterConnection
    from modelpop.domain.result import Result

__all__ = ["PrinterMonitor", "Watch"]

# A print is measured in hours and the printer reports progress in whole
# percent, so asking more often than this learns nothing and just keeps a
# radio busy.
NORMAL_INTERVAL_SECONDS = 10.0

# After a failure, ask less often. A printer that has been switched off should
# not be polled every ten seconds all night.
SLOWEST_INTERVAL_SECONDS = 120.0

# Enough consecutive failures to conclude the printer is not coming back.
# Three at a widening interval spans several minutes, which covers a reboot.
GIVE_UP_AFTER = 3


@dataclass(frozen=True, slots=True)
class Watch:
    """What the monitor knows right now."""

    status: PrinterStatus = field(default_factory=PrinterStatus)
    misses: int = 0
    watching: bool = True
    reason: str = ""
    """Why watching stopped, when it has."""

    @property
    def next_interval_seconds(self) -> float:
        """How long to wait before asking again.

        Doubles with each consecutive failure and resets on the first good
        answer, so a printer that is switched off is not polled every ten
        seconds until morning.
        """
        if not self.watching:
            return 0.0
        if not self.misses:
            return NORMAL_INTERVAL_SECONDS
        return float(min(NORMAL_INTERVAL_SECONDS * 2**self.misses, SLOWEST_INTERVAL_SECONDS))

    def describe(self) -> str:
        """A line for the panel, whatever state it is in."""
        if not self.watching:
            return self.reason or "Not watching."
        if self.misses:
            plural = "" if self.misses == 1 else "s"
            return f"{self.status.describe()} ({self.misses} missed answer{plural}; still trying.)"
        return self.status.describe()


class PrinterMonitor:
    """Asks the printer what it is doing, at a sensible rhythm.

    Stops on its own when the print finishes. A watch that keeps polling a
    finished printer forever is the sort of thing nobody notices until they
    read a log, and it is the commonest way a feature like this outstays its
    welcome.
    """

    def __init__(
        self,
        ask: Callable[[PrinterConnection], Result[PrinterStatus]],
        connection: PrinterConnection,
    ) -> None:
        """Wire the monitor to something that can ask, and a printer to ask.

        Args:
            ask: reads the printer's status. The gateway's own method, passed
                in rather than reached for, so this stays free of adapters.
            connection: which printer.
        """
        self._ask = ask
        self._connection = connection
        self._watch = Watch()
        self._listeners: list[Callable[[Watch], None]] = []

    @property
    def watch(self) -> Watch:
        """What is known right now."""
        return self._watch

    @property
    def is_watching(self) -> bool:
        """Whether there is any point calling ``poll`` again."""
        return self._watch.watching

    def on_change(self, listener: Callable[[Watch], None]) -> None:
        """Be told after every poll."""
        self._listeners.append(listener)

    def stop(self, reason: str = "Stopped watching.") -> None:
        """Give up, for a stated reason."""
        self._watch = Watch(status=self._watch.status, watching=False, reason=reason)
        self._announce()

    def poll(self) -> Watch:
        """Ask once, and work out what to do next.

        A failure is not the end of it: printers go off, networks drop, and a
        watch that quit on the first miss would be useless. It backs off and
        keeps trying, and only gives up after enough consecutive failures to
        mean something.
        """
        if not self._watch.watching:
            return self._watch

        outcome = self._ask(self._connection)
        if isinstance(outcome, Failure):
            self._missed(outcome.error)
            return self._watch

        status = outcome.unwrap()
        if status.state is PrinterState.UNREACHABLE:
            self._missed(status.detail or "The printer did not answer.")
            return self._watch

        finished = status.state in {PrinterState.FINISHED, PrinterState.FAILED}
        self._watch = Watch(
            status=status,
            misses=0,
            watching=not finished,
            reason=_ending_for(status) if finished else "",
        )
        self._announce()
        return self._watch

    def _missed(self, detail: str) -> None:
        """Record one failed attempt, and give up if there have been enough."""
        misses = self._watch.misses + 1
        if misses >= GIVE_UP_AFTER:
            self._watch = Watch(
                status=self._watch.status,
                misses=misses,
                watching=False,
                reason=f"Gave up after {misses} tries. {detail}".strip(),
            )
        else:
            self._watch = Watch(
                status=PrinterStatus(state=PrinterState.UNREACHABLE, detail=detail),
                misses=misses,
            )
        self._announce()

    def _announce(self) -> None:
        for listener in self._listeners:
            listener(self._watch)


def _ending_for(status: PrinterStatus) -> str:
    """What to say when a print stops, which depends on how it stopped."""
    if status.state is PrinterState.FAILED:
        return f"The print failed. {status.detail}".strip()
    name = status.job_name or "The print"
    return f"{name} finished."
