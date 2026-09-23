"""The CAD tools, driven with no display and no kernel.

The whole journey - draw a box, round its edges, hollow it, put a name on it,
undo half of it - runs here. The view-model imports no UI framework, which is
the only reason that is possible, and an import-linter contract keeps it so.
"""

from modelpop.application.modelling import ModellingSession
from modelpop.domain.cad_commands import EdgeSelector, Face, Fillet, Plane
from modelpop.domain.result import failure, success
from modelpop.domain.units import Length
from modelpop.generation.command_loop import CommandEditRun
from modelpop.presentation.modelling_view_model import ModellingViewModel, Outcome

from .test_modelling import FakeCompiler


def view(**kwargs) -> ModellingViewModel:
    return ModellingViewModel(ModellingSession(FakeCompiler(**kwargs)))


class TestTheJourney:
    def test_a_model_can_be_built_up_from_nothing(self):
        model = view()
        model.add_box(40, 40, 60)
        model.fillet(3, EdgeSelector.VERTICAL)
        model.hollow(1.6, Face.BOTTOM)
        model.add_text("MSI", Face.FRONT, 12, 1.2)

        labels = [line.label for line in model.state.features]
        assert len(labels) == 4
        assert "Hollow" in labels[2]
        assert "MSI" in labels[3]

    def test_the_users_own_example_reads_back_as_words(self):
        """Rob's request, as a tree he could read."""
        model = view()
        model.add_box(50, 40, 150)
        model.fillet(4, EdgeSelector.VERTICAL)
        model.hollow(2.0, Face.BOTTOM)
        model.add_text("MSI", Face.FRONT, 18, 1.5)
        model.scale_to(Length.inches(6))

        tree = " | ".join(line.label for line in model.state.features)
        assert "hollow core" not in tree.lower()  # we say it plainly, not by quoting them
        assert "Hollow to a 2 mm wall, open at the bottom" in tree
        assert 'Emboss "MSI" on the front' in tree
        assert "Scale to 152.40 mm tall" in tree

    def test_geometry_is_handed_on_after_every_rebuild(self):
        """The seam between the CAD tools and the viewport."""
        received = []
        model = ModellingViewModel(ModellingSession(FakeCompiler()), on_geometry=received.append)
        model.add_box(10, 10, 10)
        model.fillet(1)

        assert len(received) == 2

    def test_nothing_is_handed_on_when_a_command_is_refused(self):
        received = []
        model = ModellingViewModel(
            ModellingSession(FakeCompiler(refuse_containing="fillet")),
            on_geometry=received.append,
        )
        model.add_box(10, 10, 10)
        model.fillet(99)

        assert len(received) == 1, "the refused rebuild was handed on anyway"


class TestWhatTheToolbarAllows:
    def test_an_operation_is_not_offered_with_nothing_to_operate_on(self):
        """A fillet with no solid is a mistake the toolbar should prevent."""
        assert not view().can_operate

    def test_it_is_offered_once_there_is_a_shape(self):
        model = view()
        model.add_box(10, 10, 10)
        assert model.can_operate

    def test_undo_is_not_offered_on_a_new_model(self):
        assert not view().can_undo

    def test_undo_is_offered_after_a_change(self):
        model = view()
        model.add_box(10, 10, 10)
        assert model.can_undo

    def test_nothing_is_offered_without_a_kernel(self):
        model = ModellingViewModel(ModellingSession(None))
        assert not model.can_build
        assert not model.can_operate

    def test_an_unavailable_kernel_reports_itself(self):
        assert not view(available=False).can_build


class TestReportingBack:
    def test_a_successful_command_is_named_in_the_outcome(self):
        seen: list[Outcome] = []
        model = view()
        model.on_outcome(seen.append)
        model.add_box(10, 10, 10)

        assert seen[-1].message == "Add a 10 x 10 x 10 mm box"
        assert not seen[-1].went_wrong

    def test_a_refusal_is_marked_as_a_refusal_rather_than_a_failure(self):
        """The model is untouched and still correct. Saying "error" overstates it."""
        seen: list[Outcome] = []
        model = view(refuse_containing="fillet")
        model.on_outcome(seen.append)
        model.add_box(10, 10, 10)
        model.fillet(99)

        assert seen[-1].refused
        assert "unchanged" in seen[-1].detail

    def test_listeners_hear_about_every_state_change(self):
        seen = []
        model = view()
        model.on_state(seen.append)
        model.add_box(10, 10, 10)
        model.undo()

        assert len(seen) == 2
        assert seen[-1].is_empty

    def test_the_busy_flag_goes_up_and_comes_back_down(self):
        flags = []
        model = view()
        model.on_busy(flags.append)
        model.add_box(10, 10, 10)

        assert flags == [True, False]

    def test_the_busy_flag_comes_back_down_after_a_refusal(self):
        """A stuck busy flag locks the toolbar permanently."""
        flags = []
        model = view(refuse_containing="create-box")
        model.on_busy(flags.append)
        model.add_box(10, 10, 10)

        assert flags[-1] is False


class TestHistory:
    def test_undo_and_redo_walk_the_tree(self):
        model = view()
        model.add_box(10, 10, 10)
        model.fillet(1)

        model.undo()
        assert len(model.state.features) == 1
        model.redo()
        assert len(model.state.features) == 2

    def test_an_assistants_command_takes_the_same_path_and_is_undoable(self):
        """The whole point of routing an AI through the same bus."""
        model = view()
        model.add_box(10, 10, 10)
        model.apply_from_assistant(Fillet(2, EdgeSelector.TOP))

        rows = model.state.features
        assert rows[-1].by_the_assistant
        assert model.can_undo

        model.undo()
        assert len(model.state.features) == 1

    def test_starting_again_empties_everything(self):
        model = view()
        model.add_box(10, 10, 10)
        model.clear()

        assert model.state.is_empty
        assert not model.can_undo

    def test_undo_with_nothing_to_undo_says_so_rather_than_doing_nothing(self):
        seen: list[Outcome] = []
        model = view()
        model.on_outcome(seen.append)
        model.undo()

        assert seen[-1].refused


class TestReadingTheModel:
    def test_the_script_can_be_read(self):
        model = view()
        model.add_box(10, 10, 10)
        assert "create-box" in model.script()

    def test_without_a_kernel_the_script_explains_itself_as_a_comment(self):
        """Shown in a code panel, so it has to still look like source."""
        text = ModellingViewModel(ModellingSession(None)).script()
        assert text.startswith("#")


class TestDescribingAChange:
    """The AI edit path, with the model stubbed out entirely.

    What matters here is not the model but the plumbing: a described change has
    to land in the same tree, report honestly, and never leave the toolbar stuck.
    """

    def describing(self, outcome):
        """A view-model whose described changes return a fixed outcome."""
        session = ModellingSession(FakeCompiler())
        session.apply(
            __import__("modelpop.domain.cad_commands", fromlist=["CreateBox"]).CreateBox(10, 10, 10)
        )
        return ModellingViewModel(session, describe_change=lambda _: outcome)

    def run(self, **kwargs):

        return success(CommandEditRun(**kwargs))

    def test_a_successful_change_is_reported_with_what_it_did(self):
        seen: list[Outcome] = []
        model = self.describing(self.run(applied=("Round all edges by 2 mm",)))
        model.on_outcome(seen.append)
        model.describe_a_change("round the corners")

        assert "Round all edges" in seen[-1].message
        assert not seen[-1].refused

    def test_a_change_that_did_nothing_is_marked_as_refused(self):
        """Saying "done" when nothing happened is the worst possible answer."""
        seen: list[Outcome] = []
        model = self.describing(self.run(discarded=("apply-chrome",)))
        model.on_outcome(seen.append)
        model.describe_a_change("make it chrome")

        assert seen[-1].refused

    def test_a_failure_from_the_model_is_reported_rather_than_raised(self):
        seen: list[Outcome] = []
        model = self.describing(failure("The service is down", "try later"))
        model.on_outcome(seen.append)
        model.describe_a_change("round it")

        assert seen[-1].refused
        assert "service is down" in seen[-1].message

    def test_the_toolbar_is_released_afterwards(self):
        """A stuck busy flag locks every button for the rest of the session."""
        model = self.describing(self.run(applied=("something",)))
        model.describe_a_change("round it")
        assert not model.is_busy

    def test_it_is_released_after_a_failure_too(self):
        model = self.describing(failure("nope"))
        model.describe_a_change("round it")
        assert not model.is_busy

    def test_an_empty_instruction_does_nothing_at_all(self):
        asked: list[str] = []

        def record(words: str):
            asked.append(words)
            return success(CommandEditRun())

        session = ModellingSession(FakeCompiler())
        model = ModellingViewModel(session, describe_change=record)
        model.describe_a_change("   ")
        assert asked == []

    def test_without_a_provider_the_box_is_not_offered(self):
        model = view()
        model.add_box(10, 10, 10)
        assert not model.can_describe_a_change

    def test_with_a_provider_it_is_offered_once_there_is_a_shape(self):
        model = self.describing(self.run())
        assert model.can_describe_a_change

    def test_it_is_not_offered_with_nothing_to_change(self):
        session = ModellingSession(FakeCompiler())
        model = ModellingViewModel(session, describe_change=lambda _: self.run())
        assert not model.can_describe_a_change

    def test_asking_without_a_provider_says_what_to_do(self):
        seen: list[Outcome] = []
        model = view()
        model.add_box(10, 10, 10)
        model.on_outcome(seen.append)
        model.describe_a_change("round it")

        assert seen[-1].refused
        assert "Settings" in seen[-1].detail


class TestExtruding:
    """An outline with thickness, driven with no display."""

    SQUARE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))

    def test_it_joins_the_feature_tree_like_any_other_command(self):
        model = view()
        model.extrude(self.SQUARE, 6)

        assert len(model.state.features) == 1
        assert "Extrude a 4-point outline 6 mm" in model.state.features[0].label

    def test_it_can_start_a_model(self):
        """An outline is as good a way to begin a part as a box is."""
        model = view()
        model.extrude(self.SQUARE, 6)
        assert not model.state.is_empty

    def test_it_undoes(self):
        model = view()
        model.extrude(self.SQUARE, 6)
        model.undo()
        assert model.state.is_empty

    def test_the_plane_and_the_cut_flag_reach_the_command(self):
        model = view()
        model.add_box(40, 40, 40)
        model.extrude(self.SQUARE, 6, Plane.XZ, cut=True)

        assert model.state.features[-1].label.startswith("Cut")
        assert "front plane" in model.state.features[-1].label

    def test_an_outline_that_encloses_nothing_is_refused_in_plain_words(self):
        """Not passed to the kernel, whose message would say nothing useful."""
        seen: list[Outcome] = []
        model = view()
        model.on_outcome(seen.append)
        model.extrude(((0.0, 0.0), (10.0, 0.0)), 6)

        assert seen[-1].refused
        assert "three corners" in seen[-1].detail
        assert model.state.is_empty, "and nothing was recorded"

    def test_a_list_of_corners_is_accepted_as_readily_as_a_tuple(self):
        """The dialog hands over whatever the parser produced."""
        model = view()
        model.extrude([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], 6)
        assert len(model.state.features) == 1


class TestMirroringAndPatterns:
    """The two commands that make a part quick, driven with no display."""

    def plate_with_a_hole(self) -> ModellingViewModel:
        model = view()
        model.add_box(100, 40, 6)
        model.drill(5, 6, at=(-40, 0))
        return model

    def test_a_row_joins_the_tree_and_undoes(self):
        model = self.plate_with_a_hole()
        model.repeat(5, dx=20)

        assert "Repeat the last shape 5 times" in model.state.features[-1].label
        model.undo()
        assert len(model.state.features) == 2

    def test_a_ring_joins_the_tree(self):
        model = self.plate_with_a_hole()
        model.repeat_around(6)
        assert "6 copies" in model.state.features[-1].label

    def test_copies_with_no_spacing_are_refused_rather_than_built(self):
        """Otherwise the model looks unchanged and nobody can tell why."""
        seen: list[Outcome] = []
        model = self.plate_with_a_hole()
        model.on_outcome(seen.append)
        model.repeat(4)

        assert seen[-1].refused
        assert "same place" in seen[-1].message
        assert len(model.state.features) == 2, "and nothing was recorded"

    def test_one_copy_is_allowed_even_with_no_spacing(self):
        """It is a no-op, not a mistake, and the script says so."""
        model = self.plate_with_a_hole()
        model.repeat(1)
        assert len(model.state.features) == 3

    def test_mirroring_joins_the_tree(self):
        model = view()
        model.add_box(20, 40, 10, at=(10, 0, 0))
        model.mirror()

        assert "Mirror across the side plane" in model.state.features[-1].label

    def test_mirroring_can_replace_the_original(self):
        model = view()
        model.add_box(20, 40, 10, at=(10, 0, 0))
        model.mirror(Plane.XZ, keep_original=False)

        assert "replace it" in model.state.features[-1].label
