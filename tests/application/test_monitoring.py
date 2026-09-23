"""Watching a print, with no printer, no timer and no waiting.

The interface owns the clock and calls poll when it fires, which is the only
reason a watch that backs off, gives up and stops on its own is testable in
milliseconds instead of in hours.
"""

import pytest

from modelpop.application.printer_ports import PrinterState, PrinterStatus
from modelpop.domain.printer import PrinterConnection
from modelpop.domain.result import failure, success
from modelpop.presentation.monitoring import (
    GIVE_UP_AFTER,
    NORMAL_INTERVAL_SECONDS,
    SLOWEST_INTERVAL_SECONDS,
    PrinterMonitor,
    Watch,
)

PRINTER = PrinterConnection(host="10.0.0.9", serial="X1", access_code="12345678")


def answering(*replies):
    """Something that answers with each reply in turn, then repeats the last."""
    remaining = list(replies)

    def ask(_connection):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return ask


def printing(percent: float = 40.0) -> object:
    return success(
        PrinterStatus(state=PrinterState.PRINTING, job_name="bracket", percent_done=percent)
    )


def monitor(*replies) -> PrinterMonitor:
    return PrinterMonitor(answering(*replies), PRINTER)


class TestWatchingAPrint:
    def test_it_reports_what_the_printer_said(self):
        watch = monitor(printing(40)).poll()

        assert watch.status.percent_done == pytest.approx(40.0)
        assert "40%" in watch.describe()

    def test_it_keeps_watching_while_the_print_runs(self):
        watching = monitor(printing())
        watching.poll()
        assert watching.is_watching

    def test_it_tells_anyone_listening(self):
        seen: list[Watch] = []
        watching = monitor(printing())
        watching.on_change(seen.append)
        watching.poll()

        assert len(seen) == 1
        assert seen[0].status.state is PrinterState.PRINTING

    def test_it_asks_at_a_steady_rhythm_while_all_is_well(self):
        assert monitor(printing()).poll().next_interval_seconds == NORMAL_INTERVAL_SECONDS


class TestStoppingOnItsOwn:
    def test_a_finished_print_ends_the_watch(self):
        """A watch that polls a finished printer all night is how this outstays
        its welcome."""
        watch = monitor(
            success(PrinterStatus(state=PrinterState.FINISHED, job_name="bracket"))
        ).poll()

        assert not watch.watching
        assert "bracket finished" in watch.describe()

    def test_a_failed_print_ends_the_watch_and_says_so(self):
        watch = monitor(
            success(PrinterStatus(state=PrinterState.FAILED, detail="the spool ran out"))
        ).poll()

        assert not watch.watching
        assert "failed" in watch.describe()
        assert "spool" in watch.describe()

    def test_polling_after_it_has_stopped_asks_nothing(self):
        asked: list[int] = []

        def counting(_connection):
            asked.append(1)
            return success(PrinterStatus(state=PrinterState.FINISHED))

        watching = PrinterMonitor(counting, PRINTER)
        watching.poll()
        watching.poll()
        watching.poll()

        assert len(asked) == 1

    def test_it_can_be_stopped_by_hand(self):
        watching = monitor(printing())
        watching.poll()
        watching.stop("Closed the panel.")

        assert not watching.is_watching
        assert "Closed the panel" in watching.watch.describe()

    def test_a_stopped_watch_asks_for_no_further_polls(self):
        watching = monitor(printing())
        watching.stop()
        assert watching.watch.next_interval_seconds == 0.0


class TestWhenThePrinterGoesQuiet:
    def test_one_missed_answer_does_not_end_the_watch(self):
        """Printers go off, networks drop. Quitting on the first miss is useless."""
        watching = monitor(failure("no answer", "timed out"))
        watching.poll()

        assert watching.is_watching
        assert watching.watch.misses == 1

    def test_it_asks_less_often_after_each_miss(self):
        watching = monitor(failure("no answer", "timed out"))
        watching.poll()
        first = watching.watch.next_interval_seconds
        watching.poll()
        second = watching.watch.next_interval_seconds

        assert second > first > NORMAL_INTERVAL_SECONDS - 1

    def test_the_interval_never_runs_away(self):
        assert Watch(misses=40).next_interval_seconds == SLOWEST_INTERVAL_SECONDS

    def test_it_gives_up_after_enough_consecutive_misses(self):
        watching = monitor(failure("no answer", "the printer is off"))
        for _ in range(GIVE_UP_AFTER):
            watching.poll()

        assert not watching.is_watching
        assert "Gave up" in watching.watch.describe()
        assert "printer is off" in watching.watch.describe()

    def test_a_good_answer_forgives_the_misses_before_it(self):
        watching = PrinterMonitor(answering(failure("no answer"), printing(50)), PRINTER)
        watching.poll()
        watching.poll()

        assert watching.watch.misses == 0
        assert watching.watch.next_interval_seconds == NORMAL_INTERVAL_SECONDS

    def test_an_unreachable_answer_counts_as_a_miss_rather_than_a_reading(self):
        """It comes back as a success carrying bad news, which is easy to miss."""
        watching = monitor(
            success(PrinterStatus(state=PrinterState.UNREACHABLE, detail="timed out"))
        )
        watching.poll()

        assert watching.watch.misses == 1
        assert watching.is_watching

    def test_while_missing_it_says_it_is_still_trying(self):
        watching = monitor(failure("no answer"))
        watching.poll()

        assert "still trying" in watching.watch.describe()

    def test_one_miss_reads_as_singular(self):
        watching = monitor(failure("no answer"))
        watching.poll()
        assert "1 missed answer;" in watching.watch.describe()
