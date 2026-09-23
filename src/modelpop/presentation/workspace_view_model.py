"""The view-model for the main window.

Imports no UI framework - no Qt, no VTK - which is what lets it be driven by a
test with no display, no event loop and no mocking. An ``import-linter``
contract enforces that, because the moment a widget type leaks in here the
whole layer stops being testable.

Long operations are handed to a ``runner`` callable supplied by the caller. The
UI passes something that uses a worker thread; a test passes something that runs
inline. Neither this module nor the test needs to know which.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from modelpop.application.workspace import Workspace, WorkspaceState
from modelpop.domain.printer import SupportType
from modelpop.domain.readiness import Severity
from modelpop.domain.result import Result
from modelpop.domain.units import Length

if TYPE_CHECKING:
    from modelpop.domain.mesh import Mesh

__all__ = ["Notification", "WorkspaceViewModel"]

# A task and a callback for its result. The UI supplies a threaded runner; tests
# supply one that runs inline.
type Task[T] = Callable[[], Result[T]]
type Runner = Callable[[Callable[[], None]], None]


def run_inline(work: Callable[[], None]) -> None:
    """Run work on the calling thread. The default, and what tests use."""
    work()


@dataclass(frozen=True, slots=True)
class Notification:
    """Something to tell the user, at a severity the UI can style.

    ``severity`` describes the *model*; ``failed`` describes the *operation*.
    Keeping them apart matters: opening a file that turns out to be broken is a
    successful open with an alarming result, and interrupting the user with an
    error dialog for it would be wrong.
    """

    message: str
    severity: Severity = Severity.INFO
    detail: str = ""
    failed: bool = False

    @property
    def is_error(self) -> bool:
        """Whether the operation itself failed, and so warrants interrupting."""
        return self.failed


class WorkspaceViewModel:
    """Observable state and commands for the main window."""

    def __init__(self, workspace: Workspace, runner: Runner = run_inline) -> None:
        """Create the view-model.

        Args:
            workspace: the use-case layer.
            runner: how to execute long work. Defaults to running inline.
        """
        self._workspace = workspace
        self._runner = runner
        self._state = WorkspaceState()
        self._busy = False
        self._state_listeners: list[Callable[[WorkspaceState], None]] = []
        self._busy_listeners: list[Callable[[bool], None]] = []
        self._notification_listeners: list[Callable[[Notification], None]] = []

    # ------------------------------------------------------------- observing

    @property
    def state(self) -> WorkspaceState:
        """The model currently open, and what is known about it."""
        return self._state

    @property
    def is_busy(self) -> bool:
        """Whether a long operation is running."""
        return self._busy

    @property
    def mesh(self) -> Mesh | None:
        """The geometry, if any."""
        return self._state.mesh

    def on_state_changed(self, listener: Callable[[WorkspaceState], None]) -> None:
        """Be told whenever the model or its assessment changes."""
        self._state_listeners.append(listener)

    def on_busy_changed(self, listener: Callable[[bool], None]) -> None:
        """Be told when a long operation starts or finishes."""
        self._busy_listeners.append(listener)

    def on_notification(self, listener: Callable[[Notification], None]) -> None:
        """Be told about anything worth showing the user."""
        self._notification_listeners.append(listener)

    # -------------------------------------------------------------- commands

    @property
    def can_repair(self) -> bool:
        """Whether repairing would do anything useful."""
        report = self._state.readiness
        return bool(
            self._state.has_model
            and report is not None
            and any(f.fix_stage == "repair" for f in report.findings)
        )

    @property
    def can_slice(self) -> bool:
        """Whether the model is in a state worth sending to a slicer."""
        report = self._state.readiness
        return bool(self._state.has_model and report is not None and report.is_printable)

    def open(self, path: Path) -> None:
        """Load a model from disk."""
        self._run(
            lambda: self._workspace.open(path),
            done=f"Opened {path.name}",
            failed="Could not open the model",
        )

    def repair(self) -> None:
        """Make the model watertight."""
        self._run(
            lambda: self._workspace.repair(self._state),
            done="Repaired the model",
            failed="Repair failed",
        )

    def prepare_for_bed(self) -> None:
        """Clean up and place the model on the bed."""
        self._run(
            lambda: self._workspace.prepare_for_bed(self._state),
            done="Placed on the bed",
            failed="Could not prepare the model",
        )

    def scale_to_fit(self, largest: Length) -> None:
        """Resize so the longest dimension matches ``largest``."""
        self._run(
            lambda: self._workspace.scale_to_fit(self._state, largest),
            done=f"Scaled to {largest.format()}",
            failed="Could not scale the model",
        )

    def simplify(self, target_triangles: int) -> None:
        """Reduce the triangle count."""
        self._run(
            lambda: self._workspace.decimate(self._state, target_triangles),
            done=f"Simplified to about {target_triangles:,} triangles",
            failed="Could not simplify the model",
        )

    def save_as(self, path: Path) -> None:
        """Write the current model to a new file."""
        result = self._workspace.save(self._state, path)
        if result.ok:
            self._notify(Notification(f"Saved to {path.name}"))
        else:
            self._notify(
                Notification(
                    "Could not save the model", Severity.BLOCKER, result.error, failed=True
                )
            )

    def slice(self, output_dir: Path, supports: SupportType | None = None) -> None:
        """Slice the model into G-code.

        Leave ``supports`` as ``None`` to let the geometry decide.
        """
        self._run(
            lambda: self._workspace.slice(self._state, output_dir, supports),
            done="Sliced",
            failed="Slicing failed",
            describe_success=self._describe_slice,
        )

    # --------------------------------------------------------------- internal

    @staticmethod
    def _describe_slice(state: WorkspaceState) -> str:
        """Report the outcome of a slice in the user's terms."""
        report = state.last_slice
        if report is None:
            return "Sliced"
        parts = [f"Sliced in {report.predicted_duration} of print time"]
        if report.supports_generated:
            parts.append("supports generated")
        if report.warnings:
            parts.append(f"{len(report.warnings)} slicer warning(s)")
        return ", ".join(parts)

    def _run(
        self,
        task: Task[WorkspaceState],
        done: str,
        failed: str,
        describe_success: Callable[[WorkspaceState], str] | None = None,
    ) -> None:
        """Execute a task, update the state, and report what happened."""
        if self._busy:
            self._notify(Notification("Already working on something", Severity.WARNING))
            return

        self._set_busy(True)

        def work() -> None:
            try:
                result = task()
            finally:
                self._set_busy(False)

            if not result.ok:
                self._notify(Notification(failed, Severity.BLOCKER, result.error, failed=True))
                return

            self._set_state(result.unwrap())
            message = describe_success(self._state) if describe_success else done
            self._notify(Notification(message, self._severity_of_state()))

        self._runner(work)

    def _severity_of_state(self) -> Severity:
        """How loudly to report the current state, based on its findings."""
        report = self._state.readiness
        return report.verdict if report is not None else Severity.INFO

    def _set_state(self, state: WorkspaceState) -> None:
        self._state = state
        for listener in self._state_listeners:
            listener(state)

    def _set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        for listener in self._busy_listeners:
            listener(busy)

    def _notify(self, notification: Notification) -> None:
        for listener in self._notification_listeners:
            listener(notification)
