"""Getting a finished job onto the printer.

The last stage of the pipeline and the one that makes ModelPop an application
rather than a converter: open a model, prepare it, slice it, and send it -
without ever opening Bambu Studio.

**A send is the only thing in this application that does something physical.**
Everything else changes a file. This starts a machine moving in another room,
on a spool of filament that costs money, and it cannot be undone from here. So
the port is shaped so that *offering* to send and *actually* sending are
different calls, and the default gateway does not send at all - see
``docs/00-plan.md``, Phase 2: "dry-run by default".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from modelpop.domain.printer import PrinterConnection
from modelpop.domain.result import Result

__all__ = [
    "PrintJob",
    "PrinterGateway",
    "PrinterState",
    "PrinterStatus",
    "Submission",
]


class PrinterState(Enum):
    """What the printer is doing, as far as we can tell."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"
    UNREACHABLE = "unreachable"

    @property
    def is_busy(self) -> bool:
        """Whether sending another job now would be a mistake."""
        return self in {PrinterState.PRINTING, PrinterState.PAUSED}


@dataclass(frozen=True, slots=True)
class PrintJob:
    """One thing to send to one printer.

    ``start_now`` is deliberately **off** by default and deliberately separate
    from the upload. Putting a file on the printer is reversible - delete it -
    and starting it is not, so a caller has to ask for the second thing
    explicitly rather than getting it as a side effect of the first.
    """

    file_path: Path
    connection: PrinterConnection
    name: str = ""
    start_now: bool = False
    plate: int = 1
    bed_levelling: bool = True
    flow_calibration: bool = True

    @property
    def filename(self) -> str:
        """What the file will be called on the printer.

        Taken from the source unless the caller named it. A printer's file list
        is a flat list on a small screen, so a name that says what the thing is
        beats one that says ``model``.
        """
        chosen = self.name.strip() or self.file_path.stem
        kept = "".join(c for c in chosen if c.isalnum() or c in " -_.")
        # Runs of dots collapse and leading ones go. A separator can never
        # survive the filter above, so this is not a traversal guard - it is
        # what stops "../../etc/passwd" landing on the printer as the
        # unreadable "....etcpasswd".
        safe = re.sub(r"\.{2,}", ".", kept).strip(" .")
        return f"{safe or 'modelpop'}{self.file_path.suffix}"

    @property
    def problem(self) -> str | None:
        """Why this job cannot be sent, in words the user can act on."""
        if not self.file_path.is_file():
            return f"{self.file_path.name} is not there any more. Slice it again."
        return self.connection.problem


@dataclass(frozen=True, slots=True)
class Submission:
    """What happened when a job was sent.

    ``started`` is reported separately from ``uploaded`` because the two fail
    separately: a file can land on the printer and the print still not begin,
    and telling the user "sent" when only half of that is true sends them to
    the wrong machine to find out why.
    """

    uploaded: bool = False
    started: bool = False
    filename: str = ""
    was_dry_run: bool = False
    detail: str = ""

    def describe(self) -> str:
        """A line for the user, saying exactly how far it got."""
        if self.was_dry_run:
            return (
                f"Dry run: {self.filename} was not sent. "
                "Turn off dry run in Settings to send it for real."
            )
        if self.uploaded and self.started:
            return f"{self.filename} was sent and the print has started."
        if self.uploaded:
            return f"{self.filename} is on the printer. Start it from the printer's screen."
        return f"{self.filename} was not sent. {self.detail}".strip()


@dataclass(frozen=True, slots=True)
class PrinterStatus:
    """What the printer says about itself."""

    state: PrinterState = PrinterState.UNKNOWN
    job_name: str = ""
    percent_done: float = 0.0
    minutes_remaining: int = 0
    nozzle_celsius: float = 0.0
    bed_celsius: float = 0.0
    detail: str = ""

    def describe(self) -> str:
        """A line for the status bar."""
        if self.state is PrinterState.UNREACHABLE:
            return f"The printer did not answer. {self.detail}".strip()
        if self.state.is_busy:
            name = self.job_name or "a job"
            left = f", {self.minutes_remaining} min left" if self.minutes_remaining else ""
            return f"Printing {name} - {self.percent_done:.0f}% done{left}."
        return f"The printer is {self.state.value}."


@runtime_checkable
class PrinterGateway(Protocol):
    """Sending a job to a printer, and asking what it is doing.

    Implementations must not raise. A printer that is off, on another subnet,
    or has had its access code changed is an ordinary Tuesday, not an
    exceptional circumstance, and every one of those belongs in the ``Result``.
    """

    def is_available(self) -> bool:
        """Whether this gateway could send anything at all right now."""
        ...

    def describe(self) -> str:
        """How jobs would be sent, for the settings panel and the logs."""
        ...

    def send(self, job: PrintJob) -> Result[Submission]:
        """Put the file on the printer, and start it only if asked to."""
        ...

    def status(self, connection: PrinterConnection) -> Result[PrinterStatus]:
        """Ask the printer what it is doing."""
        ...
