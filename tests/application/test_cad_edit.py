"""Editing a part by describing the change.

The script is the document, so an edit is a new script put through exactly the
same gates. That matters: an edit that skipped a check the original generation
applied could quietly break a part that was fine.
"""

from modelpop.application.ai_ports import AiSettings
from modelpop.application.cad_ports import Dimension, DimensionTable
from modelpop.application.generation_ports import PartGenerator
from modelpop.application.workspace import Workspace, WorkspaceState
from modelpop.domain import Length
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.result import failure
from modelpop.generation import build_edit
from modelpop.generation.cad_loop import CadLoopGenerator, edit_part
from modelpop.presentation import WorkspaceViewModel

from .test_cad_loop import RIGHT, WRONG_SIZE, FakeKernel, FakeOps, ScriptedModel, code
from .test_workspace import FakeIO

PLAIN = "from build123d import *\nresult = Box(60, 40, 8)"

SPEC = DimensionTable((Dimension("width", Length.mm(60)), Dimension("depth", Length.mm(40))))


class TestTheEditPrompt:
    def test_the_whole_script_is_sent_back(self):
        """A model asked to change code it cannot see invents what it forgot."""
        prompt = build_edit(PLAIN, "round the corners")
        assert "Box(60, 40, 8)" in prompt
        assert "from build123d import *" in prompt

    def test_the_instruction_is_stated_plainly(self):
        assert "round the corners" in build_edit(PLAIN, "round the corners")

    def test_it_asks_for_everything_else_to_stay_put(self):
        prompt = build_edit(PLAIN, "round the corners")
        assert "Change only what was asked for" in prompt

    def test_it_asks_for_the_whole_script_back(self):
        assert "complete modified script" in build_edit(PLAIN, "x")

    def test_dimensions_that_must_survive_the_edit_are_restated(self):
        prompt = build_edit(PLAIN, "round the corners", SPEC)
        assert "must still hold" in prompt
        assert "60.00 mm" in prompt


class TestEditing:
    def test_an_edit_runs_the_model_and_returns_a_new_part(self):
        model = ScriptedModel([RIGHT])
        run = edit_part(PLAIN, "round the corners", model, FakeKernel(), FakeOps()).unwrap()
        assert run.succeeded

    def test_an_edit_is_put_through_the_same_gates(self):
        """An edit that broke the spec must be caught, not accepted."""
        model = ScriptedModel([WRONG_SIZE])
        run = edit_part(
            PLAIN,
            "make it smaller",
            model,
            FakeKernel(),
            FakeOps(),
            SPEC,
            settings=AiSettings(max_attempts=1),
        ).unwrap()

        assert not run.succeeded
        assert "55.00 mm" in run.attempts[0].report.feedback

    def test_a_failed_edit_is_corrected_like_any_other_attempt(self):
        model = ScriptedModel([WRONG_SIZE, RIGHT])
        run = edit_part(PLAIN, "round the corners", model, FakeKernel(), FakeOps(), SPEC).unwrap()
        assert run.succeeded
        assert len(run.attempts) == 2

    def test_an_empty_script_is_refused(self):
        result = edit_part("  ", "round it", ScriptedModel([RIGHT]), FakeKernel())
        assert not result.ok
        assert "not generated from a script" in result.error

    def test_an_empty_instruction_is_refused(self):
        result = edit_part(PLAIN, "   ", ScriptedModel([RIGHT]), FakeKernel())
        assert not result.ok
        assert "describe the change" in result.error

    def test_an_unconfigured_provider_says_what_to_do(self):
        model = ScriptedModel([RIGHT], configured=False)
        result = edit_part(PLAIN, "round it", model, FakeKernel())
        assert not result.ok
        assert "Settings" in result.error


class TestThroughTheWorkspace:
    def workspace(self, model: ScriptedModel) -> Workspace:
        return Workspace(
            FakeIO(),
            FakeOps(),
            None,
            PrinterProfile.p2s(),
            generator=CadLoopGenerator(model, FakeKernel(), FakeOps()),
        )

    def test_a_generated_part_can_be_edited(self):
        model = ScriptedModel([RIGHT])
        workspace = self.workspace(model)
        generated = workspace.generate_part("a bracket").unwrap()

        edited = workspace.edit_part(generated, "round the corners")
        assert edited.ok
        assert edited.unwrap().last_generation is not None

    def test_an_imported_model_cannot_be_edited_by_description(self):
        """It has no script to rewrite, and saying so beats failing obscurely."""
        workspace = self.workspace(ScriptedModel([RIGHT]))
        imported = WorkspaceState(mesh=workspace.generate_part("x").unwrap().mesh)

        result = workspace.edit_part(imported, "round the corners")
        assert not result.ok
        assert "only a generated part" in result.error

    def test_an_edit_invalidates_a_stale_slice(self, tmp_path):
        model = ScriptedModel([RIGHT])
        workspace = self.workspace(model)
        generated = workspace.generate_part("a bracket").unwrap()

        edited = workspace.edit_part(generated, "round it").unwrap()
        assert edited.last_slice is None

    def test_the_edited_part_is_re_assessed(self):
        model = ScriptedModel([RIGHT])
        workspace = self.workspace(model)
        generated = workspace.generate_part("a bracket").unwrap()

        edited = workspace.edit_part(generated, "round it").unwrap()
        assert edited.readiness is not None


class TestThroughTheViewModel:
    def view_model(self, model: ScriptedModel) -> WorkspaceViewModel:
        return WorkspaceViewModel(
            Workspace(
                FakeIO(),
                FakeOps(),
                None,
                PrinterProfile.p2s(),
                generator=CadLoopGenerator(model, FakeKernel(), FakeOps()),
            )
        )

    def test_editing_is_offered_only_after_a_part_is_generated(self):
        view_model = self.view_model(ScriptedModel([RIGHT]))
        assert not view_model.can_edit_by_description

        view_model.generate_part("a bracket")
        assert view_model.can_edit_by_description

    def test_an_edit_is_reported_as_a_change_not_a_generation(self):
        """The wording should match what the user just did."""
        view_model = self.view_model(ScriptedModel([RIGHT]))
        notes = []
        view_model.generate_part("a bracket")
        view_model.on_notification(notes.append)
        view_model.edit_part("round the corners")

        assert notes[-1].message.startswith("Changed")

    def test_a_generation_is_reported_as_a_generation(self):
        view_model = self.view_model(ScriptedModel([RIGHT]))
        notes = []
        view_model.on_notification(notes.append)
        view_model.generate_part("a bracket")

        assert notes[-1].message.startswith("Generated")

    def test_the_report_says_how_many_attempts_it_took_and_what_it_cost(self):
        view_model = self.view_model(ScriptedModel([RIGHT]))
        notes = []
        view_model.on_notification(notes.append)
        view_model.generate_part("a bracket")

        assert "first try" in notes[-1].message
        assert "$" in notes[-1].message

    def test_several_attempts_are_reported_as_such(self):
        view_model = self.view_model(ScriptedModel([code("this is not python"), RIGHT]))
        notes = []
        view_model.on_notification(notes.append)
        view_model.generate_part("a bracket")

        assert "2 attempts" in notes[-1].message


class TestTheSettingsTheUserChose:
    """Limits set in Settings must reach the loop that spends the money."""

    class RecordingGenerator:
        """Stands in for the real loop and remembers how it was called."""

        def __init__(self) -> None:
            self.settings: list[AiSettings | None] = []

        def is_ready(self) -> bool:
            return True

        def generate(self, request, *, printer, table=None, settings=None):
            self.settings.append(settings)
            return failure("not today")

        def edit(self, script, instruction, *, printer, table=None, settings=None):
            self.settings.append(settings)
            return failure("not today")

    def test_the_view_models_limits_are_passed_to_generation(self):
        generator = self.RecordingGenerator()
        view_model = WorkspaceViewModel(
            Workspace(FakeIO(), FakeOps(), None, PrinterProfile.p2s(), generator=generator)
        )
        view_model.ai_settings = AiSettings(max_attempts=7)
        view_model.generate_part("a bracket")

        assert generator.settings == [AiSettings(max_attempts=7)]

    def test_a_recording_generator_satisfies_the_port(self):
        """If this drifts, the fake above stops proving anything."""
        assert isinstance(self.RecordingGenerator(), PartGenerator)
