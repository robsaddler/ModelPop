"""The view-model for the CAD tools.

Imports no UI framework, so the whole modelling journey - draw a box, round its
edges, hollow it, put a name on it, undo half of that - is drivable by a test
with no display and no event loop.

It sits *beside* the workspace view-model rather than inside it, because the two
own different things. The workspace owns a mesh, which may have come from a
file, a repository or a generator; this owns a feature tree, which is a model
that can still be changed. When the tree rebuilds, the resulting mesh is handed
to the workspace so the viewport, the readiness panel and the slicer all work on
it without knowing where it came from.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from modelpop.application.modelling import ModellingSession, ModelState
from modelpop.domain.cad_commands import (
    Chamfer,
    CreateBox,
    CreateCylinder,
    CreateSphere,
    EdgeSelector,
    Face,
    Fillet,
    Hollow,
    Move,
    Rotate,
    ScaleTo,
    TextOnSurface,
)
from modelpop.domain.commands import Origin
from modelpop.domain.result import Failure, Result

if TYPE_CHECKING:
    from pathlib import Path

    from modelpop.domain.commands import Command
    from modelpop.domain.mesh import Mesh
    from modelpop.domain.units import Length
    from modelpop.generation.command_loop import CommandEditRun

__all__ = ["ModellingViewModel", "Outcome"]

type Runner = Callable[[Callable[[], None]], None]


def run_inline(work: Callable[[], None]) -> None:
    """Run work on the calling thread. The default, and what tests use."""
    work()


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened when a command was tried.

    ``refused`` is deliberately separate from a plain failure: a refused command
    means the model is untouched and still correct, which is reassuring rather
    than alarming, and the interface should say so in those terms.
    """

    message: str
    detail: str = ""
    refused: bool = False

    @property
    def went_wrong(self) -> bool:
        """Whether anything at all failed."""
        return self.refused or bool(self.detail)


class ModellingViewModel:
    """Observable state and commands for the CAD tools."""

    def __init__(
        self,
        session: ModellingSession | None = None,
        runner: Runner = run_inline,
        on_geometry: Callable[[Mesh], None] | None = None,
        describe_change: Callable[[str], Result[CommandEditRun]] | None = None,
    ) -> None:
        """Create the view-model.

        Args:
            session: the use-case layer.
            runner: how to execute a rebuild. Defaults to running inline; the
                interface supplies one that uses a worker thread, because a
                rebuild is a subprocess and takes a second or two.
            on_geometry: called with the new mesh after every successful
                rebuild, so the viewport and the print pipeline see it.
            describe_change: asks a language model to change the model. Passed
                in rather than built here, because the presentation layer must
                not know which provider is in use.
        """
        self._session = session or ModellingSession()
        self._runner = runner
        self._on_geometry = on_geometry
        self._describe = describe_change
        self._busy = False
        self._state_listeners: list[Callable[[ModelState], None]] = []
        self._outcome_listeners: list[Callable[[Outcome], None]] = []
        self._busy_listeners: list[Callable[[bool], None]] = []

    # ------------------------------------------------------------- observing

    @property
    def state(self) -> ModelState:
        """The model as it currently stands."""
        return self._session.state

    @property
    def is_busy(self) -> bool:
        """Whether a rebuild is running."""
        return self._busy

    @property
    def can_describe_a_change(self) -> bool:
        """Whether the "tell it what to change" box should be offered."""
        return self._describe is not None and self.can_operate

    @property
    def can_build(self) -> bool:
        """Whether the CAD tools can do anything at all.

        False when the kernel failed to load, which disables the toolbar rather
        than letting every button fail at the click.
        """
        return self._session.can_build

    @property
    def can_undo(self) -> bool:
        """Whether there is a change to step back from."""
        return self.state.can_undo and not self._busy

    @property
    def can_redo(self) -> bool:
        """Whether there is a change to step forward to."""
        return self.state.can_redo and not self._busy

    @property
    def can_operate(self) -> bool:
        """Whether an operation has something to operate on.

        A fillet with no solid is a mistake the toolbar should prevent rather
        than a message the user has to read.
        """
        return not self.state.is_empty and not self._busy

    def on_state(self, listener: Callable[[ModelState], None]) -> None:
        """Be told whenever the model changes."""
        self._state_listeners.append(listener)

    def on_outcome(self, listener: Callable[[Outcome], None]) -> None:
        """Be told the result of each command."""
        self._outcome_listeners.append(listener)

    def on_busy(self, listener: Callable[[bool], None]) -> None:
        """Be told when a rebuild starts and stops."""
        self._busy_listeners.append(listener)

    # ----------------------------------------------------------- the toolbar

    def add_box(
        self,
        width: float,
        depth: float,
        height: float,
        at: tuple[float, float, float] = (0.0, 0.0, 0.0),
        *,
        cut: bool = False,
    ) -> None:
        """Add a rectangular block, or cut a pocket with one."""
        self._apply(CreateBox(width, depth, height, *at, cut=cut))

    def add_cylinder(
        self,
        radius: float,
        height: float,
        at: tuple[float, float, float] = (0.0, 0.0, 0.0),
        *,
        cut: bool = False,
    ) -> None:
        """Add a cylinder, or drill a hole with one."""
        self._apply(CreateCylinder(radius, height, *at, cut=cut))

    def add_sphere(
        self,
        radius: float,
        at: tuple[float, float, float] = (0.0, 0.0, 0.0),
        *,
        cut: bool = False,
    ) -> None:
        """Add a sphere, or scoop one out."""
        self._apply(CreateSphere(radius, *at, cut=cut))

    def drill(self, diameter: float, depth: float, at: tuple[float, float] = (0.0, 0.0)) -> None:
        """Drill a hole straight through.

        Named for what it is rather than what it does internally. The cylinder
        is made longer than the stated depth so it passes right through instead
        of leaving a skin the user then has to notice.
        """
        self._apply(CreateCylinder(diameter / 2, depth * 2, at[0], at[1], 0.0, cut=True))

    def fillet(self, radius: float, edges: EdgeSelector = EdgeSelector.ALL) -> None:
        """Round edges."""
        self._apply(Fillet(radius, edges))

    def chamfer(self, distance: float, edges: EdgeSelector = EdgeSelector.ALL) -> None:
        """Cut a flat bevel on edges."""
        self._apply(Chamfer(distance, edges))

    def hollow(self, wall: float, opening: Face | None = None) -> None:
        """Hollow the part out, optionally leaving a face open to drain."""
        self._apply(Hollow(wall, opening))

    def move(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> None:
        """Shift the part."""
        self._apply(Move(dx, dy, dz))

    def rotate(self, degrees: float, axis: str = "Z") -> None:
        """Turn the part about an axis."""
        self._apply(Rotate(degrees, axis))

    def scale_to(self, height: Length) -> None:
        """Resize so the part is a stated height."""
        self._apply(ScaleTo(height))

    def add_text(
        self,
        text: str,
        face: Face = Face.FRONT,
        size: float = 10.0,
        depth: float = 1.0,
        *,
        raised: bool = True,
    ) -> None:
        """Emboss or engrave text on a face."""
        self._apply(TextOnSurface(text, face, size, depth, raised))

    def apply_from_assistant(self, command: Command) -> None:
        """Apply a command a language model asked for.

        The same path as a toolbar click, which is the whole point: an AI edit
        is undoable because it is an ordinary command, recorded with who asked.
        """
        self._apply(command, Origin.ASSISTANT)

    def describe_a_change(self, instruction: str) -> None:
        """Ask a language model to change the model, in the user's own words.

        The model replies with the same typed commands the toolbar emits, so
        the change joins the feature tree, is marked as the assistant's, and is
        undoable. There is no separate AI history and no special case.
        """
        if not instruction.strip():
            return
        if self._describe is None:
            self._announce(
                Outcome(
                    "Describing a change needs an AI provider",
                    "Add an API key in Settings.",
                    refused=True,
                )
            )
            return

        self._run_described(instruction)

    def _run_described(self, instruction: str) -> None:
        """Run one described change off the interface thread."""
        if self._busy:
            self._announce(Outcome("Still rebuilding; that was ignored.", refused=True))
            return

        self._set_busy(True)
        describe = self._describe

        def finish() -> None:
            try:
                assert describe is not None
                outcome = describe(instruction)
                if isinstance(outcome, Failure):
                    self._announce(Outcome(outcome.reason, outcome.detail, refused=True))
                    return

                run = outcome.unwrap()
                mesh = self.state.mesh
                if run.changed_anything and mesh is not None and self._on_geometry is not None:
                    self._on_geometry(mesh)
                self._announce(
                    Outcome(run.summary(), run.detail(), refused=not run.changed_anything)
                )
            finally:
                self._set_busy(False)
                self._announce_state()

        self._runner(finish)

    # -------------------------------------------------------------- history

    def undo(self) -> None:
        """Step back one change."""
        self._run("Undone", self._session.undo)

    def redo(self) -> None:
        """Step forward again."""
        self._run("Redone", self._session.redo)

    def save_to(self, path: Path) -> None:
        """Write the model to a project file."""
        outcome = self._session.save_to(path)
        if isinstance(outcome, Failure):
            self._announce(Outcome(outcome.reason, outcome.detail, refused=True))
            return
        self._announce(Outcome(f"Saved to {outcome.unwrap().name}."))

    def open_from(self, path: Path) -> None:
        """Read a project file and rebuild it.

        Rebuilt on the worker like any other change, because opening a project
        runs the whole tree and a large one takes as long as a rebuild does.
        """
        self._run(f"Opened {path.name}", lambda: self._session.open_from(path))

    @property
    def can_save(self) -> bool:
        """Whether there is a model worth writing down."""
        return not self.state.is_empty and not self._busy

    def clear(self) -> None:
        """Start a new model."""
        self._session.clear()
        self._announce_state()
        self._announce(Outcome("Started a new model."))

    # ------------------------------------------------------------- colours

    @property
    def can_split_colours(self) -> bool:
        """Whether this model has a second colour worth separating."""
        return self._session.has_second_colour and not self._busy

    def split_colours(self, into: Path) -> None:
        """Write the model out as one file per filament.

        Two builds, so it goes to the worker like any other rebuild. The result
        is reported with what each part weighs, because the interesting fact is
        almost always how *little* the lettering is.
        """
        if self._busy:
            self._announce(Outcome("Still rebuilding; that was ignored.", refused=True))
            return

        self._set_busy(True)

        def finish() -> None:
            try:
                outcome = self._session.colour_parts(into)
                if isinstance(outcome, Failure):
                    self._announce(Outcome(outcome.reason, outcome.detail, refused=True))
                    return
                parts = outcome.unwrap()
                self._announce(Outcome(parts.describe(), f"Written to {into}."))
            finally:
                self._set_busy(False)
                self._announce_state()

        self._runner(finish)

    # -------------------------------------------------------------- reading

    def script(self) -> str:
        """The source this model compiles to, or why it cannot be shown."""
        result = self._session.script()
        return result.unwrap() if result.ok else f"# {result.error}"

    # ------------------------------------------------------------- internal

    def _apply(self, command: Command, origin: Origin = Origin.USER) -> None:
        self._run(command.describe(), lambda: self._session.apply(command, origin))

    def _run(self, label: str, work: Callable[[], Result[ModelState]]) -> None:
        """Run a rebuild off the interface thread and report what happened."""
        if self._busy:
            # A rebuild is a subprocess. Queueing clicks would let a user stack
            # up ten of them and then wait through all ten.
            self._announce(Outcome("Still rebuilding; that click was ignored.", refused=True))
            return

        self._set_busy(True)

        def finish() -> None:
            try:
                self._report(label, work())
            finally:
                self._set_busy(False)
                self._announce_state()

        self._runner(finish)

    def _report(self, label: str, result: Result[ModelState]) -> None:
        if isinstance(result, Failure):
            self._announce(Outcome(result.reason, result.detail, refused=True))
            return

        mesh = result.unwrap().mesh
        if mesh is not None and self._on_geometry is not None:
            self._on_geometry(mesh)
        self._announce(Outcome(label))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for listener in self._busy_listeners:
            listener(busy)

    def _announce_state(self) -> None:
        for listener in self._state_listeners:
            listener(self.state)

    def _announce(self, outcome: Outcome) -> None:
        for listener in self._outcome_listeners:
            listener(outcome)
