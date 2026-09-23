"""The CAD tools, driven with no display and no kernel.

The whole journey - draw a box, round its edges, hollow it, put a name on it,
undo half of it - runs here. The view-model imports no UI framework, which is
the only reason that is possible, and an import-linter contract keeps it so.
"""

from modelpop.application.modelling import ModellingSession
from modelpop.domain.cad_commands import EdgeSelector, Face, Fillet
from modelpop.domain.units import Length
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

        assert seen[-1].message == "Box 10 x 10 x 10 mm"
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
