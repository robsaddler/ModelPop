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

from modelpop.application.ai_ports import AiSettings
from modelpop.application.workspace import Workspace, WorkspaceState
from modelpop.domain.printer import PrinterConnection, PrinterProfile, SupportType
from modelpop.domain.readiness import Severity
from modelpop.domain.result import Result
from modelpop.domain.units import Length

if TYPE_CHECKING:
    from modelpop.application.cad_ports import DimensionTable
    from modelpop.application.mesh_generation_ports import GenerationOptions, Progress
    from modelpop.application.printer_ports import PrinterStatus
    from modelpop.application.reconstruction_ports import (
        Progress as ReconstructionProgress,
    )
    from modelpop.application.reconstruction_ports import (
        ReconstructionOptions,
    )
    from modelpop.domain.mesh import Mesh
    from modelpop.domain.photo_set import PhotoSet

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
        self._ai_settings = AiSettings()
        self._connection = PrinterConnection()
        self._state = WorkspaceState()
        self._busy = False
        self._state_listeners: list[Callable[[WorkspaceState], None]] = []
        self._busy_listeners: list[Callable[[bool], None]] = []
        self._notification_listeners: list[Callable[[Notification], None]] = []

    @property
    def printer(self) -> PrinterProfile:
        """The machine everything is assessed against.

        Exposed because the print preview draws the build volume, and reaching
        past the view-model into the workspace for it would put the UI back in
        touch with the layer this exists to keep it away from.
        """
        return self._workspace.printer

    @property
    def ai_settings(self) -> AiSettings:
        """The limits generation runs under."""
        return self._ai_settings

    @ai_settings.setter
    def ai_settings(self, settings: AiSettings) -> None:
        """Adopt limits the user changed in Settings.

        These are held here rather than in the workspace because they are the
        user's, not the application's: they change while the app is running, and
        a limit the user raised must apply to the very next run.
        """
        self._ai_settings = settings

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
    def can_generate(self) -> bool:
        """Whether a part can be generated right now."""
        return self._workspace.can_generate

    def generate_part(self, request: str, table: DimensionTable | None = None) -> None:
        """Write a parametric part from a description."""
        self._run(
            lambda: self._workspace.generate_part(request, table, self._ai_settings),
            done="Generated",
            failed="Could not generate the part",
            describe_success=self._describe_generation,
        )

    @staticmethod
    def _describe_generation(state: WorkspaceState) -> str:
        """Report a generation run in the user's terms."""
        return WorkspaceViewModel._describe_run(state, "Generated")

    @staticmethod
    def _describe_edit(state: WorkspaceState) -> str:
        """Report an edit in the user's terms."""
        return WorkspaceViewModel._describe_run(state, "Changed")

    @staticmethod
    def _describe_run(state: WorkspaceState, verb: str) -> str:
        """How many attempts it took, whether it hit the spec, and what it cost."""
        run = state.last_generation
        if run is None:
            return verb
        attempts = len(run.attempts)
        tries = "first try" if attempts == 1 else f"{attempts} attempts"
        verdict = "as specified" if run.succeeded else "close, but not exact"
        return f"{verb} {verdict} in {tries}, about ${run.total_cost_usd:.2f}"

    @property
    def can_edit_by_description(self) -> bool:
        """Whether the open part has a script that can be rewritten."""
        return self._state.last_generation is not None and self._workspace.can_generate

    def edit_part(self, instruction: str, table: DimensionTable | None = None) -> None:
        """Change the open part by describing the change."""
        self._run(
            lambda: self._workspace.edit_part(self._state, instruction, table, self._ai_settings),
            done="Edited",
            failed="Could not make that change",
            describe_success=self._describe_edit,
        )

    @property
    def can_slice(self) -> bool:
        """Whether the model is in a state worth sending to a slicer."""
        report = self._state.readiness
        return bool(self._state.has_model and report is not None and report.is_printable)

    def adopt(self, mesh: Mesh) -> None:
        """Take geometry that came from somewhere other than a file.

        The seam between the CAD tools and everything downstream: the feature
        tree rebuilds, hands its mesh here, and the viewport, the readiness
        panel and the slicer all pick it up without knowing where it came from.

        Not routed through the runner. The expensive part already happened in
        the rebuild; assessing a mesh that is already in memory is fast, and
        putting it on a second thread would only mean the viewport shows the
        old shape for a frame.
        """
        self._set_state(self._workspace.adopt(mesh))

    @property
    def can_generate_a_mesh(self) -> bool:
        """Whether a picture could be turned into a shape right now."""
        return self._workspace.can_generate_a_mesh

    def describe_mesh_generation(self) -> str:
        """The state of the mesh generator, for Settings."""
        return self._workspace.describe_mesh_generation()

    def generate_from_image(
        self,
        image: Path,
        options: GenerationOptions | None = None,
        on_progress: Progress | None = None,
    ) -> None:
        """Turn a picture into a model.

        Routed through the runner like every other long operation, because this
        one takes minutes and a frozen window reads as a crash.
        """
        self._run(
            lambda: self._workspace.generate_from_image(image, options, on_progress),
            done=f"Made a model from {image.name}",
            failed="Could not make a model from that picture",
        )

    @property
    def can_reconstruct(self) -> bool:
        """Whether several photographs could be turned into a model right now."""
        return self._workspace.can_reconstruct and not self._busy

    def describe_reconstruction(self) -> str:
        """The state of the reconstruction tools, for Settings."""
        return self._workspace.describe_reconstruction()

    def reconstruct_from_photos(
        self,
        photos: PhotoSet,
        options: ReconstructionOptions | None = None,
        on_progress: ReconstructionProgress | None = None,
    ) -> None:
        """Measure a model from several photographs.

        Through the runner like every other long operation, and more so: this
        one runs for minutes with the card held, so a frozen window would not
        merely look like a crash, it would look like one for a long time.
        """
        self._run(
            lambda: self._workspace.reconstruct_from_photos(photos, options, on_progress),
            done=f"Built a model from {len(photos)} photographs",
            failed="Could not build a model from those photographs",
        )

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

    @property
    def can_rescue_detail(self) -> bool:
        """Whether this model has a texture worth baking into its surface."""
        return (
            self._workspace.can_rescue_detail
            and self._state.textured_path is not None
            and not self._busy
        )

    def rescue_detail(self, depth_mm: float = 0.4) -> None:
        """Turn the model's colour into relief.

        An ordinary operation on the bus like any other, so it re-assesses,
        re-draws and can be undone by reopening - which matters more here than
        elsewhere, because how deep the relief should be is a judgement nobody
        can make in advance.
        """
        self._run(
            lambda: self._workspace.rescue_detail(self._state, depth_mm),
            done="Baked the texture into the surface",
            failed="The detail could not be baked in",
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

    # ---------------------------------------------------------------- sending

    @property
    def printer_connection(self) -> PrinterConnection:
        """How to reach the printer. Empty until the user sets it up."""
        return self._connection

    @printer_connection.setter
    def printer_connection(self, connection: PrinterConnection) -> None:
        """Adopt an address the user typed in Settings."""
        self._connection = connection

    @property
    def can_send_to_printer(self) -> bool:
        """Whether there is a sliced job and somewhere to send it."""
        return (
            self._workspace.can_send_to_printer
            and self._state.last_slice is not None
            and not self._busy
        )

    def describe_printer_route(self) -> str:
        """How a job would reach the printer."""
        return self._workspace.describe_printer_route()

    def send_to_printer(self, *, start_now: bool = False, for_real: bool = False) -> None:
        """Send the last sliced job, and say exactly how far it got.

        Off the interface thread, because an unreachable printer costs the
        whole connection timeout and a frozen window during it is how a user
        concludes the application has crashed.

        The outcome is reported from the submission rather than from whether
        the call failed: a file can land on the printer and the print still
        not begin, and "sent" would be the wrong word for that.
        """
        if self._busy:
            self._notify(Notification("Already working on something", Severity.WARNING))
            return

        connection = self._connection
        self._set_busy(True)

        def work() -> None:
            try:
                outcome = self._workspace.send_to_printer(
                    self._state, connection, start_now=start_now, for_real=for_real
                )
            finally:
                self._set_busy(False)

            if not outcome.ok:
                self._notify(
                    Notification(
                        "The job was not sent", Severity.BLOCKER, outcome.error, failed=True
                    )
                )
                return

            # A job that uploaded but did not start is not a failure - the
            # model is on the printer - so it is reported as news rather than
            # as an error, with the detail saying what is left to do.
            submission = outcome.unwrap()
            arrived = submission.uploaded or submission.was_dry_run
            self._notify(
                Notification(
                    submission.describe(),
                    Severity.INFO if arrived else Severity.WARNING,
                    submission.detail,
                )
            )

        self._runner(work)

    def printer_status(self, connection: PrinterConnection) -> Result[PrinterStatus]:
        """Ask the printer what it is doing, and hand back the answer.

        Returned rather than announced, and taking the printer as an argument
        rather than reading the stored one. Both are for the monitoring panel,
        which polls this from its own worker thread on a clock of its own: a
        method that announced through the view-model's listeners would put a
        reading on the status bar every ten seconds for the length of a print.
        """
        return self._workspace.printer_status(connection)

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
