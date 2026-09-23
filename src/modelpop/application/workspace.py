"""The workspace: what the user is working on, and what can be done to it.

This is the use-case layer. It knows the *order* things happen in - load, then
inspect, then repair, then slice - and nothing about how any of them are
implemented. Every dependency arrives through a port.

Deliberately synchronous. Threading belongs to the caller, because only the
caller knows whether it has a UI event loop to protect. Keeping it out of here
means the whole use-case layer can be tested with no scheduler at all.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import PrinterProfile, SupportType
from modelpop.domain.readiness import ReadinessReport, assess
from modelpop.domain.result import Result, failure, success
from modelpop.domain.units import Length

if TYPE_CHECKING:
    from modelpop.application.ai_ports import AiSettings, ChatProvider
    from modelpop.application.cad_ports import CadKernel, DimensionTable
    from modelpop.application.ports import MeshIO, MeshOps, Slicer, SliceReport
    from modelpop.generation.cad_loop import CadGenerationRun

__all__ = ["Workspace", "WorkspaceState"]

# A print does not need more detail than this, and the viewport stops being
# responsive well before it. See the triangle-budget readiness rule.
DEFAULT_TRIANGLE_BUDGET = 300_000


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    """Everything known about the model currently open.

    Immutable: each operation produces a new state. The UI can therefore hold on
    to a previous one, diff two of them, or discard a change without any risk of
    having mutated shared data underneath itself.
    """

    mesh: Mesh | None = None
    source_path: Path | None = None
    readiness: ReadinessReport | None = None
    last_slice: SliceReport | None = None
    last_generation: CadGenerationRun | None = None
    """How the part was generated, when it was. Kept so the user can read the
    script, see what was corrected, and know what the run cost."""

    @property
    def has_model(self) -> bool:
        """Whether anything is loaded."""
        return self.mesh is not None and not self.mesh.is_empty

    @property
    def title(self) -> str:
        """What to show in the window title bar."""
        if self.source_path is not None:
            return self.source_path.name
        return "Untitled" if self.has_model else "No model"

    def describe(self) -> str:
        """A one-line summary for the status bar."""
        if not self.has_model or self.mesh is None:
            return "No model loaded."
        bounds = self.mesh.bounds
        return (
            f"{self.mesh.triangle_count:,} triangles, "
            f"{bounds.width.format(places=1)} x {bounds.depth.format(places=1)} x "
            f"{bounds.height.format(places=1)}"
        )


class Workspace:
    """Operations on the model the user has open.

    Every method returns a new :class:`WorkspaceState` rather than mutating one,
    and reports expected failures as a ``Failure`` rather than raising.
    """

    def __init__(  # one argument per port, which is the point
        self,
        mesh_io: MeshIO,
        mesh_ops: MeshOps,
        slicer: Slicer | None = None,
        printer: PrinterProfile | None = None,
        cad_kernel: CadKernel | None = None,
        ai: ChatProvider | None = None,
        ai_settings: AiSettings | None = None,
    ) -> None:
        """Wire the workspace to its ports.

        Every optional port degrades to a clear message rather than a crash:
        without a slicer you can still inspect and repair, and without an AI
        provider you can still do everything but generate.

        Args:
            mesh_io: reading and writing mesh files.
            mesh_ops: geometry operations.
            slicer: turns a model into G-code.
            printer: the target printer; a P2S by default.
            cad_kernel: runs generated CAD scripts.
            ai: the model that writes those scripts.
            ai_settings: attempt and spend limits.
        """
        self._io = mesh_io
        self._ops = mesh_ops
        self._slicer = slicer
        self._printer = printer or PrinterProfile.p2s()
        self._cad = cad_kernel
        self._ai = ai
        self._ai_settings = ai_settings

    @property
    def can_generate(self) -> bool:
        """Whether both halves of the generation path are present."""
        return (
            self._cad is not None
            and self._ai is not None
            and self._ai.is_configured()
            and self._cad.is_available()
        )

    @property
    def printer(self) -> PrinterProfile:
        """The printer everything is assessed against."""
        return self._printer

    # ------------------------------------------------------------------- open

    def open(self, path: Path) -> Result[WorkspaceState]:
        """Load a model and assess it immediately.

        Assessing on load is deliberate: the user should learn that their
        download is broken before they try to print it, not after.
        """
        loaded = self._io.load(path)
        if not loaded.ok:
            return loaded  # type: ignore[return-value]

        mesh = loaded.unwrap()
        return success(
            WorkspaceState(
                mesh=mesh,
                source_path=path,
                readiness=self._assess(mesh),
            )
        )

    def adopt(self, mesh: Mesh, source_path: Path | None = None) -> WorkspaceState:
        """Take a mesh that came from somewhere other than a file.

        Used by the generation pipelines, which produce geometry directly.
        """
        return WorkspaceState(mesh=mesh, source_path=source_path, readiness=self._assess(mesh))

    def save(self, state: WorkspaceState, path: Path) -> Result[Path]:
        """Write the current model to disk."""
        if state.mesh is None:
            return failure("Nothing to save", "no model is open")
        return self._io.save(state.mesh, path)

    # -------------------------------------------------------------- transform

    def repair(self, state: WorkspaceState) -> Result[WorkspaceState]:
        """Make the model watertight, and re-assess it."""
        if state.mesh is None:
            return failure("Nothing to repair", "no model is open")

        repaired = self._ops.repair(state.mesh)
        if not repaired.ok:
            return repaired  # type: ignore[return-value]
        return success(self._with_mesh(state, repaired.unwrap()))

    def scale_to_fit(self, state: WorkspaceState, largest: Length) -> Result[WorkspaceState]:
        """Resize so the longest dimension matches ``largest``.

        This is what "about six inches tall" means in practice.
        """
        if state.mesh is None:
            return failure("Nothing to scale", "no model is open")
        if largest.millimetres <= 0:
            return failure("Invalid size", "the target size must be greater than zero")
        return success(self._with_mesh(state, state.mesh.scaled_to_fit(largest)))

    def decimate(
        self, state: WorkspaceState, target: int = DEFAULT_TRIANGLE_BUDGET
    ) -> Result[WorkspaceState]:
        """Reduce the triangle count to something a printer can use."""
        if state.mesh is None:
            return failure("Nothing to simplify", "no model is open")

        reduced = self._ops.decimate(state.mesh, target)
        if not reduced.ok:
            return reduced  # type: ignore[return-value]
        return success(self._with_mesh(state, reduced.unwrap()))

    def prepare_for_bed(self, state: WorkspaceState) -> Result[WorkspaceState]:
        """Clean up, sit the model on the bed and centre it.

        The placement a slicer expects, and the one thing worth doing to every
        model regardless of where it came from.
        """
        if state.mesh is None:
            return failure("Nothing to prepare", "no model is open")
        prepared = self._ops.normalise(state.mesh).dropped_to_bed()
        return success(self._with_mesh(state, prepared))

    # --------------------------------------------------------------- generate

    def generate_part(
        self, request: str, table: DimensionTable | None = None
    ) -> Result[WorkspaceState]:
        """Write a parametric part from a description, and check it measures up.

        Returns the best attempt even when none fully passed: a part that is
        nearly right is something the user can edit, and nothing is not.
        """
        if self._cad is None:
            return failure(
                "The CAD kernel is unavailable",
                "build123d could not be loaded. Reinstall the application's dependencies.",
            )
        if self._ai is None:
            return failure(
                "No AI provider configured",
                "Add an API key in Settings before generating a part.",
            )

        # imported here rather than at module scope to avoid an import cycle
        from modelpop.generation.cad_loop import generate_part as run_loop

        outcome = run_loop(
            request=request,
            provider=self._ai,
            kernel=self._cad,
            mesh_ops=self._ops,
            table=table,
            printer=self._printer,
            settings=self._ai_settings,
        )
        if not outcome.ok:
            return outcome  # type: ignore[return-value]

        run = outcome.unwrap()
        if run.best is None or run.best.result is None:
            return failure(
                "Nothing usable was produced",
                run.attempts[-1].error if run.attempts else "no attempt ran",
            )

        mesh = run.best.result.mesh
        return success(
            WorkspaceState(
                mesh=mesh,
                source_path=None,
                readiness=self._assess(mesh),
                last_generation=run,
            )
        )

    def edit_part(
        self, state: WorkspaceState, instruction: str, table: DimensionTable | None = None
    ) -> Result[WorkspaceState]:
        """Change the open part by describing the change.

        Only possible for a part this application generated: the edit rewrites
        the script, and a model imported from a file has no script to rewrite.
        """
        run = state.last_generation
        if run is None or run.best is None:
            return failure(
                "This part cannot be edited by description",
                "only a generated part has a script to change. Generate one, or use "
                "the geometry tools on an imported model.",
            )
        if self._cad is None or self._ai is None:
            return failure(
                "Editing needs the CAD kernel and an AI provider",
                "Add an API key in Settings.",
            )

        # imported here rather than at module scope to avoid an import cycle
        from modelpop.generation.cad_loop import edit_part as run_edit

        outcome = run_edit(
            script=run.best.script,
            instruction=instruction,
            provider=self._ai,
            kernel=self._cad,
            mesh_ops=self._ops,
            table=table,
            printer=self._printer,
            settings=self._ai_settings,
        )
        if not outcome.ok:
            return outcome  # type: ignore[return-value]

        edited = outcome.unwrap()
        if edited.best is None or edited.best.result is None:
            return failure(
                "The edit produced nothing usable",
                edited.attempts[-1].error if edited.attempts else "no attempt ran",
            )

        mesh = edited.best.result.mesh
        return success(
            replace(
                state,
                mesh=mesh,
                readiness=self._assess(mesh),
                last_generation=edited,
                last_slice=None,
            )
        )

    # ------------------------------------------------------------------ slice

    def slice(
        self,
        state: WorkspaceState,
        output_dir: Path,
        supports: SupportType | None = None,
    ) -> Result[WorkspaceState]:
        """Slice the current model into G-code.

        Args:
            state: the model to slice.
            output_dir: where the G-code and project file should go.
            supports: leave as ``None`` to decide from the geometry, which is
                almost always what you want. See :meth:`_choose_supports`.

        Refuses to slice a model the readiness rules have blocked. Sending
        known-broken geometry to a slicer wastes the user's time and teaches
        them to ignore the warnings.
        """
        if state.mesh is None:
            return failure("Nothing to slice", "no model is open")
        if self._slicer is None or not self._slicer.is_available():
            return failure(
                "No slicer available",
                "Bambu Studio was not found. Install it, or choose another slicer in settings.",
            )
        if state.readiness is not None and not state.readiness.is_printable:
            blockers = "; ".join(f.message for f in state.readiness.blockers)
            return failure("The model is not ready to print", blockers)

        # imported here rather than at module scope to avoid an import cycle
        from modelpop.application.ports import SliceJob

        output_dir.mkdir(parents=True, exist_ok=True)
        model_file = output_dir / "model.stl"
        written = self._io.save(self._place_on_bed(state.mesh), model_file)
        if not written.ok:
            return written  # type: ignore[return-value]

        wanted = supports if supports is not None else self._choose_supports(state)
        job = SliceJob(
            model_path=model_file,
            printer=self._printer,
            output_dir=output_dir,
            supports=wanted,
        )

        sliced = self._slicer.slice(job)

        # Enabling supports enlarges the effective footprint, and a large model
        # that fits without them can be refused with them. Rather than handing
        # the user an unexplained failure, drop the supports and say so.
        if (
            not sliced.ok
            and wanted is not SupportType.NONE
            and self._is_footprint_refusal(sliced.error)
        ):
            retried = self._slicer.slice(replace(job, supports=SupportType.NONE))
            if retried.ok:
                report = replace(
                    retried.unwrap(),
                    warnings=(
                        *retried.unwrap().warnings,
                        "Supports were turned off: with them the model no longer fitted "
                        "the build plate. Scale it down slightly if you need them.",
                    ),
                )
                return success(replace(state, last_slice=report))

        if not sliced.ok:
            return sliced  # type: ignore[return-value]
        return success(replace(state, last_slice=sliced.unwrap()))

    @staticmethod
    def _is_footprint_refusal(message: str) -> bool:
        """Whether the slicer refused the plate because nothing fitted on it."""
        return "fully inside" in message or "plate is empty" in message

    # --------------------------------------------------------------- internal

    def _assess(self, mesh: Mesh) -> ReadinessReport:
        """Measure the mesh once and run every rule against the result."""
        return assess(self._ops.inspect(mesh), self._printer)

    def _with_mesh(self, state: WorkspaceState, mesh: Mesh) -> WorkspaceState:
        """Replace the geometry and re-assess, discarding any stale slice."""
        return replace(state, mesh=mesh, readiness=self._assess(mesh), last_slice=None)

    @staticmethod
    def _choose_supports(state: WorkspaceState) -> SupportType:
        """Enable supports only when the geometry actually overhangs.

        Two reasons, one obvious and one learned the hard way. The obvious one:
        supports on a model that does not need them waste time and filament.

        The one that cost an evening: **enabling supports enlarges the effective
        footprint**, and Bambu Studio rejects the whole plate with "no object
        fully inside it" when the result no longer fits. A 152 mm cube slices
        happily without supports and is refused with them - even though a cube
        has no overhangs at all and the supports would have been empty.
        """
        report = state.readiness
        if report is None:
            return SupportType.TREE_AUTO
        needs_them = any(f.rule == "overhangs" for f in report.findings)
        return SupportType.TREE_AUTO if needs_them else SupportType.NONE

    def _place_on_bed(self, mesh: Mesh) -> Mesh:
        """Move the model from our coordinates into the printer's.

        ModelPop works with the model centred on the origin and sitting on z=0,
        which is printer-agnostic and the natural convention for a viewport.
        Bambu's bed origin is a *corner*, so a model centred on our origin sits
        at negative coordinates and the slicer rejects the plate as empty.

        The offset belongs here rather than in the domain: it is a fact about a
        particular printer, not about geometry.
        """
        centre_x, centre_y = self._printer.bed_centre
        seated = mesh.dropped_to_bed()
        return seated.translated(
            centre_x / mesh.unit.millimetres, centre_y / mesh.unit.millimetres, 0.0
        )
