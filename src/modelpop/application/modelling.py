"""The open parametric model: a feature tree that can be changed and undone.

This is where ADR-0001 stops being a document and starts being load-bearing.
Every change to a parametric model goes through here, whoever asked for it: a
toolbar click, a language model's typed command, or a replay. They are the same
commands on the same bus, so an AI edit is undoable for free and the history is
the model.

**A change that will not build is refused, not applied.** The obvious
implementation appends the feature and rebuilds; when the rebuild fails the user
is left holding a broken model and an error, with the thing that broke it
already in their history. Instead the command is applied, the tree rebuilt, and
the change rolled back if the kernel refuses it. The model is therefore always
in a state that builds - which is the invariant that makes undo trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from modelpop.application.cad_ports import Part
from modelpop.domain.cad_commands import command_from
from modelpop.domain.commands import CommandBus, Document, DocumentHistory, Origin
from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from modelpop.application.cad_ports import FeatureCompiler, SolidMeasurements
    from modelpop.application.ports import MeshIO
    from modelpop.application.project_ports import ProjectStore
    from modelpop.domain.commands import Command, Feature
    from modelpop.domain.mesh import Mesh

__all__ = ["ColourParts", "FeatureLine", "ModelState", "ModellingSession"]


@dataclass(frozen=True, slots=True)
class ColourParts:
    """A model split into the two filaments it would print in.

    Two separate solids rather than one, so a slicer can be told which filament
    each takes. Compiled apart rather than cut apart: a boolean on two meshes to
    recover the lettering would be slow, fragile at the seam, and would throw
    away the exactness that made it worth building in a kernel.
    """

    body: Mesh
    decoration: Mesh
    body_path: Path | None = None
    decoration_path: Path | None = None

    @property
    def decoration_fraction(self) -> float:
        """How much of the model is the second colour.

        Usually a fraction of a percent - lettering on a large body - which is
        exactly why the AMS purge matters so much for it.
        """
        total = self.body.volume + self.decoration.volume
        return self.decoration.volume / total if total > 0 else 0.0

    def describe(self) -> str:
        """A line for the user."""
        return (
            f"Split into two: the body at {self.body.volume / 1000:.1f} cm3 and the "
            f"lettering at {self.decoration.volume / 1000:.2f} cm3 "
            f"({self.decoration_fraction:.1%} of it)."
        )


@dataclass(frozen=True, slots=True)
class FeatureLine:
    """One row of the feature tree, ready to draw."""

    index: int
    label: str
    origin: Origin
    suppressed: bool = False
    understood: bool = True
    """False for a feature saved by a newer build. Shown, greyed, not silently lost."""

    @property
    def by_the_assistant(self) -> bool:
        """Whether a language model asked for this, rather than the user.

        Shown in the tree, and what an "undo everything the assistant did"
        action would select on.
        """
        return self.origin is Origin.ASSISTANT


@dataclass(frozen=True, slots=True)
class ModelState:
    """Everything known about the parametric model currently open."""

    document: Document = field(default_factory=Document)
    mesh: Mesh | None = None
    measurements: SolidMeasurements | None = None
    can_undo: bool = False
    can_redo: bool = False
    undo_label: str = ""
    redo_label: str = ""
    # What has been undone, oldest first. Shown greyed beneath the tree so an
    # undone step is visibly waiting rather than simply gone.
    undone: tuple[str, ...] = ()
    rebuild_seconds: float = 0.0

    @property
    def has_geometry(self) -> bool:
        """Whether there is something to look at."""
        return self.mesh is not None and not self.mesh.is_empty

    @property
    def is_empty(self) -> bool:
        """Whether the tree has no features at all."""
        return len(self.document) == 0

    @property
    def features(self) -> tuple[FeatureLine, ...]:
        """The tree, as rows.

        Built here rather than in the view so the wording is testable and so
        two different views cannot describe the same model differently.
        """
        return tuple(_line_for(index, f) for index, f in enumerate(self.document.features))

    def describe(self) -> str:
        """A line for the status bar."""
        if self.is_empty:
            return "No model. Start with a box, a cylinder or a sphere."
        count = len(self.document)
        noun = "feature" if count == 1 else "features"
        if self.measurements is None:
            return f"{count} {noun}, not built."
        size = self.measurements
        return (
            f"{count} {noun} - {size.width.format(places=1)} x "
            f"{size.depth.format(places=1)} x {size.height.format(places=1)}"
        )


def _line_for(index: int, feature: Feature) -> FeatureLine:
    """One feature as a row, whether or not this build understands it."""
    command = command_from(feature)
    return FeatureLine(
        index=index,
        label=command.describe() if command else f"{feature.name} (not supported here)",
        origin=feature.origin,
        suppressed=feature.suppressed,
        understood=command is not None,
    )


class ModellingSession:
    """Operations on the parametric model the user has open.

    Holds the bus, so it is the one place a model is mutated. Every method
    returns a fresh :class:`ModelState`; nothing here hands out anything the
    caller could change underneath it.
    """

    def __init__(
        self,
        compiler: FeatureCompiler | None = None,
        projects: ProjectStore | None = None,
        mesh_io: MeshIO | None = None,
    ) -> None:
        """Wire the session to a compiler and a place to keep projects.

        Args:
            compiler: rebuilds the tree into geometry. Optional, so the app
                still starts when the CAD kernel failed to load - the tree can
                be read and edited, it just cannot be built.
            projects: reads and writes project files.
            mesh_io: writes the colour parts out. Optional: without it they can
                still be built and looked at, just not saved.
        """
        self._compiler = compiler
        self._projects = projects
        self._io = mesh_io
        self._bus = CommandBus()
        self._state = ModelState()

    @property
    def state(self) -> ModelState:
        """The model as it currently stands."""
        return self._state

    @property
    def can_build(self) -> bool:
        """Whether a rebuild is possible right now."""
        return self._compiler is not None and self._compiler.is_available()

    # ------------------------------------------------------------- changing

    def apply(self, command: Command, origin: Origin = Origin.USER) -> Result[ModelState]:
        """Apply a command, rebuild, and keep it only if the rebuild worked.

        A command the kernel refuses leaves the model exactly as it was. The
        alternative - appending it anyway and reporting an error - gives the
        user a broken model *and* a broken history, and they then have to
        realise that undoing is what fixes it.
        """
        self._bus.execute(command, origin)

        rebuilt = self._rebuild()
        if rebuilt.ok:
            return rebuilt

        self._bus.undo()
        self._state = _state_from(self._bus, self._state)
        return failure(
            f"{command.describe()} could not be applied",
            f"{rebuilt.error} The model is unchanged.",
        )

    def undo(self) -> Result[ModelState]:
        """Step back one change."""
        if not self._bus.history.can_undo:
            return failure("There is nothing to undo")
        self._bus.undo()
        return self._rebuild()

    def redo(self) -> Result[ModelState]:
        """Step forward again."""
        if not self._bus.history.can_redo:
            return failure("There is nothing to redo")
        self._bus.redo()
        return self._rebuild()

    def clear(self) -> ModelState:
        """Start a new model, discarding the tree and its history."""
        self._bus = CommandBus()
        self._state = ModelState()
        return self._state

    def save_to(self, path: Path) -> Result[Path]:
        """Write the feature tree to a file.

        The tree is the model, so this is the whole project. There is no
        geometry in the file - it is rebuilt on opening, which is also how a
        saved model picks up a later build's improvements to an operation.
        """
        if self._projects is None:
            return failure("Saving is unavailable", "No project store was configured.")
        if not self._bus.document.features:
            return failure("There is nothing to save", "The model has no steps yet.")
        return self._projects.save(self._bus.document, path)

    def open_from(self, path: Path) -> Result[ModelState]:
        """Read a project file and rebuild it."""
        if self._projects is None:
            return failure("Opening is unavailable", "No project store was configured.")

        read = self._projects.load(path)
        if not read.ok:
            return read  # type: ignore[return-value]

        saved = read.unwrap()
        rebuilt = self.load(saved.document)
        if rebuilt.ok and not saved.is_complete:
            # Loaded, but not whole. Said rather than left for the user to
            # notice that a step is missing from the shape.
            return failure(
                f"{path.name} opened, but {len(saved.unknown)} step(s) are not supported here",
                f"Unsupported: {', '.join(saved.unknown)}. They are still in the file.",
            )
        return rebuilt

    def load(self, document: Document) -> Result[ModelState]:
        """Open a saved feature tree.

        Rebuilt immediately rather than lazily, so a document that will not
        build says so when it is opened rather than at the first edit.
        """
        self._bus = CommandBus(_history_for(document))
        return self._rebuild()

    # ------------------------------------------------------------- colours

    @property
    def has_second_colour(self) -> bool:
        """Whether this model has raised lettering that could print separately."""
        return self._compiler is not None and self._compiler.has_second_colour(self._bus.document)

    def colour_parts(self, into: Path | None = None) -> Result[ColourParts]:
        """Build the model as two solids, one per filament.

        Two builds rather than one, so it costs two subprocesses - which is the
        right trade for an export nobody runs in a loop, and far better than
        recovering the lettering by subtracting meshes afterwards.
        """
        if self._compiler is None:
            return failure(
                "The CAD kernel is unavailable",
                "build123d could not be loaded, so the model cannot be split.",
            )
        if not self.has_second_colour:
            return failure(
                "There is nothing to print in a second colour",
                "Add raised text to the model first.",
            )

        built: dict[Part, Mesh] = {}
        for part in (Part.BODY, Part.DECORATION):
            outcome = self._compiler.build(self._bus.document, part=part)
            if not outcome.ok:
                return failure(
                    f"The {part.value} could not be built",
                    outcome.error,
                )
            built[part] = outcome.unwrap().mesh

        parts = ColourParts(body=built[Part.BODY], decoration=built[Part.DECORATION])
        if into is None or self._io is None:
            return success(parts)
        return self._write(parts, into)

    def _write(self, parts: ColourParts, into: Path) -> Result[ColourParts]:
        """Save both parts beside each other, named for what they are."""
        assert self._io is not None
        into.mkdir(parents=True, exist_ok=True)

        written: dict[str, Path] = {}
        for name, mesh in (("body", parts.body), ("lettering", parts.decoration)):
            target = into / f"{name}.stl"
            saved = self._io.save(mesh, target)
            if not saved.ok:
                return failure(f"The {name} could not be saved", saved.error)
            written[name] = target

        return success(
            replace(
                parts,
                body_path=written["body"],
                decoration_path=written["lettering"],
            )
        )

    # -------------------------------------------------------------- reading

    def script(self) -> Result[str]:
        """The source this model compiles to.

        For the "show me what this actually is" panel. A model you cannot
        inspect is one you cannot trust, and this is cheap to offer.
        """
        if self._compiler is None:
            return failure(
                "The CAD kernel is unavailable",
                "build123d could not be loaded, so the model cannot be compiled.",
            )
        return self._compiler.script_for(self._bus.document)

    # ------------------------------------------------------------- internal

    def _rebuild(self) -> Result[ModelState]:
        """Replay the whole tree and adopt the result."""
        if self._compiler is None:
            self._state = _state_from(self._bus, self._state)
            return failure(
                "The CAD kernel is unavailable",
                "build123d could not be loaded. Reinstall the application's dependencies.",
            )

        if not self._bus.document.active_features:
            # An empty tree still has a history. Building this state without
            # one was why undoing the *first* step disabled Redo: there was
            # nothing to rebuild, so the flags that drive the buttons were
            # never carried across and defaulted to False.
            self._state = ModelState(
                document=self._bus.document,
                can_undo=self._bus.history.can_undo,
                can_redo=self._bus.history.can_redo,
                undo_label=self._bus.history.undo_label or "",
                redo_label=self._bus.history.redo_label or "",
                undone=self._bus.history.undone_labels,
            )
            return success(self._state)

        built = self._compiler.build(self._bus.document)
        if not built.ok:
            return built  # type: ignore[return-value]

        result = built.unwrap()
        self._state = ModelState(
            document=self._bus.document,
            mesh=result.mesh,
            measurements=result.measurements,
            can_undo=self._bus.history.can_undo,
            can_redo=self._bus.history.can_redo,
            undo_label=self._bus.history.undo_label or "",
            redo_label=self._bus.history.redo_label or "",
            undone=self._bus.history.undone_labels,
            rebuild_seconds=result.duration_seconds,
        )
        return success(self._state)


def _state_from(bus: CommandBus, previous: ModelState) -> ModelState:
    """A state carrying the current tree but the previous geometry.

    Used after a refused change: the document is back to what it was, and the
    geometry that is on screen is still correct for it.
    """
    return ModelState(
        document=bus.document,
        mesh=previous.mesh,
        measurements=previous.measurements,
        can_undo=bus.history.can_undo,
        can_redo=bus.history.can_redo,
        undo_label=bus.history.undo_label or "",
        redo_label=bus.history.redo_label or "",
    )


def _history_for(document: Document) -> DocumentHistory:
    """A history whose single entry is the loaded document.

    Opening a file is not an undoable step: there is nothing before it to go
    back to, and offering undo there would look like a way to lose the file.
    """
    return DocumentHistory(document)
