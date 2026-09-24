"""A scene of several objects, and moving one without moving the others.

This is the thing the application could not do. Every shape compiled into a
single boolean union, so a cube and a sphere were one part with two lumps:
there was nothing to select and moving either moved both. Asked directly - how
do I move them independently - and the honest answer was that you could not.

What is tested here is the contract that replaced it: each object is built from
its own features, a command lands on the selected object alone, and the scene
still costs one rebuild however many objects are in it.
"""

import pytest

from modelpop.application.modelling import ModellingSession
from modelpop.domain.units import Length
from modelpop.presentation.modelling_view_model import ModellingViewModel, Outcome

from .test_modelling import FakeCompiler


def scene(**kwargs) -> ModellingViewModel:
    return ModellingViewModel(ModellingSession(FakeCompiler(**kwargs)))


def ids(model: ModellingViewModel) -> list[str]:
    return [body.id for body in model.bodies]


class TestAddingObjects:
    def test_the_first_shape_is_the_first_object(self):
        model = scene()
        model.add_box(10, 10, 10)
        assert ids(model) == ["body-1"]

    def test_a_second_shape_is_a_second_object(self):
        """Not unioned into the first, which is the whole point."""
        model = scene()
        model.add_box(10, 10, 10)
        model.add_sphere(5)

        assert len(model.bodies) == 2
        assert ids(model) == ["body-1", "body-2"]

    def test_whatever_was_added_last_is_what_you_are_holding(self):
        """A maker adds a shape and expects the next thing to happen to it."""
        model = scene()
        model.add_box(10, 10, 10)
        model.add_sphere(5)
        assert model.selected == "body-2"

    def test_objects_are_named_after_what_started_them(self):
        model = scene()
        model.add_box(10, 10, 10)
        model.add_cylinder(4, 20)
        assert [b.label for b in model.bodies] == ["Box", "Cylinder"]

    def test_a_cut_goes_into_the_selected_object_rather_than_making_a_new_one(self):
        """Drilling a hole is not adding an object to the scene."""
        model = scene()
        model.add_box(20, 20, 20)
        model.add_cylinder(3, 40, cut=True)

        assert len(model.bodies) == 1, "the cutter became an object of its own"
        assert len(model.state.document.features_for("body-1")) == 2


class TestWhatACommandLandsOn:
    def build_two(self) -> ModellingViewModel:
        model = scene()
        model.add_box(20, 20, 20)
        model.add_sphere(8)
        return model

    def test_an_operation_changes_only_the_selected_object(self):
        model = self.build_two()
        model.move(0, 0, 30)

        assert len(model.state.document.features_for("body-1")) == 1
        assert len(model.state.document.features_for("body-2")) == 2

    def test_selecting_a_different_object_redirects_the_next_command(self):
        model = self.build_two()
        model.select("body-1")
        model.move(0, 0, 30)

        assert len(model.state.document.features_for("body-1")) == 2
        assert len(model.state.document.features_for("body-2")) == 1

    def test_the_objects_really_are_in_different_places(self):
        """Through the compiler, not just in the tree."""
        model = self.build_two()
        first, second = model.bodies
        assert first.bounds.min_x != second.bounds.min_x

    def test_selecting_announces_so_the_viewport_can_follow(self):
        model = self.build_two()
        seen: list[object] = []
        model.on_state(seen.append)
        model.select("body-1")

        assert seen, "nothing was told that the selection changed"

    def test_selecting_what_is_already_selected_changes_nothing(self):
        model = self.build_two()
        seen: list[object] = []
        model.on_state(seen.append)
        model.select(model.selected)

        assert seen == []


class TestRemovingAndCopying:
    def build_two(self) -> ModellingViewModel:
        model = scene()
        model.add_box(20, 20, 20)
        model.add_sphere(8)
        return model

    def test_deleting_takes_one_object_and_leaves_the_other(self):
        model = self.build_two()
        model.delete_selected()

        assert ids(model) == ["body-1"]

    def test_deleting_takes_everything_that_shaped_it(self):
        model = self.build_two()
        model.move(0, 0, 10)
        model.delete_selected()

        assert model.state.document.features_for("body-2") == ()

    def test_the_selection_lands_on_something_real_afterwards(self):
        """A toolbar pointed at nothing is how a click does nothing silently."""
        model = self.build_two()
        model.delete_selected()

        assert model.selected == "body-1"
        assert model.selected_body is not None

    def test_deleting_undoes(self):
        model = self.build_two()
        model.delete_selected()
        model.undo()

        assert len(model.bodies) == 2

    def test_a_copy_is_an_object_of_its_own(self):
        model = self.build_two()
        model.duplicate_selected()

        assert len(model.bodies) == 3
        assert model.selected == model.bodies[-1].id

    def test_a_copy_does_not_sit_exactly_on_the_original(self):
        model = self.build_two()
        model.duplicate_selected()
        original, copy = model.bodies[1], model.bodies[2]

        assert original.bounds.min_x != copy.bounds.min_x

    def test_moving_a_copy_leaves_the_original_alone(self):
        """A copy of the features, not a second reference to one object."""
        model = self.build_two()
        model.duplicate_selected()
        before = model.bodies[1].bounds.min_z
        model.move(0, 0, 40)

        assert model.bodies[1].bounds.min_z == pytest.approx(before)

    def test_renaming_changes_what_it_is_called(self):
        model = self.build_two()
        model.rename_selected("Lid")

        assert model.selected_body is not None
        assert model.selected_body.label == "Lid"

    def test_renaming_undoes(self):
        model = self.build_two()
        model.rename_selected("Lid")
        model.undo()

        assert model.selected_body is not None
        assert model.selected_body.label == "Sphere"


class TestTheCostOfAScene:
    def test_a_scene_of_many_objects_is_still_one_rebuild(self):
        """Building each object separately would multiply every edit by the
        number of things on the plate. An OCCT rebuild is two seconds."""
        compiler = FakeCompiler()
        model = ModellingViewModel(ModellingSession(compiler))
        model.add_box(10, 10, 10)
        model.add_sphere(5)
        model.add_cylinder(3, 10)
        builds = len(compiler.builds)

        model.move(1, 0, 0)
        assert len(compiler.builds) == builds + 1, "one nudge cost more than one rebuild"


class TestTheTreeReadsAsAScene:
    def test_steps_are_grouped_under_the_object_they_shape(self):
        model = scene()
        model.add_box(10, 10, 10)
        model.fillet(1)
        model.add_sphere(5)

        grouped = model.state.features_by_object
        assert [label for label, _ in grouped] == ["Box", "Sphere"]
        assert [len(lines) for _, lines in grouped] == [2, 1]

    def test_every_step_knows_which_object_it_belongs_to(self):
        model = scene()
        model.add_box(10, 10, 10)
        model.add_sphere(5)

        assert {line.body for line in model.state.features} == {"body-1", "body-2"}


class TestWhatTheChangeControlsActOn:
    """Asked directly: "the Change it controls apply to what? Entire object?"

    Every one of them applies to the **selected object**, whole, and to nothing
    else on the plate. The question was worth asking because the panel used to
    say "Change it" without ever saying what *it* was.
    """

    def build_two(self) -> ModellingViewModel:
        model = scene()
        model.add_box(20, 20, 20)
        model.add_sphere(8)
        model.select("body-1")
        return model

    @pytest.mark.parametrize(
        "change",
        [
            lambda m: m.fillet(1),
            lambda m: m.chamfer(1),
            lambda m: m.hollow(1.5),
            lambda m: m.move(1, 0, 0),
            lambda m: m.rotate(15),
            lambda m: m.scale_to(Length.mm(40)),
            lambda m: m.mirror(),
            lambda m: m.repeat_around(4),
        ],
        ids=[
            "round",
            "chamfer",
            "hollow",
            "move",
            "turn",
            "scale",
            "mirror",
            "repeat around",
        ],
    )
    def test_it_lands_on_the_selected_object_and_no_other(self, change):
        model = self.build_two()
        before = len(model.state.document.features_for("body-2"))

        change(model)

        assert len(model.state.document.features_for("body-1")) == 2
        assert len(model.state.document.features_for("body-2")) == before

    def test_it_applies_to_the_whole_object_not_part_of_it(self):
        """There is no sub-selection: a fillet rounds the object's edges."""
        model = self.build_two()
        model.fillet(1)

        step = model.state.document.features_for("body-1")[-1]
        assert step.name == "fillet"
        assert step.body == "body-1"

    def test_nothing_selected_refuses_rather_than_changing_the_first_one(self):
        """Clicking empty space puts everything down; the toolbar must agree.

        It used to fall back to the first object, so a click in empty space
        followed by Round quietly changed something the user was not even
        looking at.
        """
        model = self.build_two()
        model.select("")
        outcomes: list[Outcome] = []
        model.on_outcome(outcomes.append)

        model.fillet(1)

        assert outcomes and outcomes[-1].refused
        assert "selected" in outcomes[-1].message.lower()
        assert len(model.state.document.features_for("body-1")) == 1

    def test_the_very_first_shape_needs_nothing_selected(self):
        """An empty scene has nothing to select, and must still start."""
        model = scene()
        model.add_box(10, 10, 10)

        assert len(model.bodies) == 1


class TestNewThingsStandOnThePlate:
    """A new object arrives on the bed, not halfway through it.

    Shapes are built centred on the origin, so every new one appeared with half
    of itself below the plate - reported as the application sinking every model
    it was given. Lifting each by half its height puts it where anybody would
    expect a thing they just added to be.
    """

    def test_a_box_stands_on_the_bed(self):
        model = scene()
        model.add_box(40, 40, 40)
        assert model.state.document.active_features[-1].parameters["z"] == pytest.approx(20.0)

    def test_a_sphere_rests_on_the_bed(self):
        model = scene()
        model.add_sphere(15)
        assert model.state.document.active_features[-1].parameters["z"] == pytest.approx(15.0)

    def test_a_cylinder_stands_on_the_bed(self):
        model = scene()
        model.add_cylinder(8, 30)
        assert model.state.document.active_features[-1].parameters["z"] == pytest.approx(15.0)

    def test_a_cutter_is_left_exactly_where_it_was_aimed(self):
        """A drill is positioned to cut something; moving it moves the hole."""
        model = scene()
        model.add_box(40, 40, 40)
        model.add_cylinder(3, 60, cut=True)
        assert model.state.document.active_features[-1].parameters["z"] == pytest.approx(0.0)

    def test_a_position_the_caller_asked_for_is_honoured(self):
        model = scene()
        model.add_box(10, 10, 10, at=(5.0, 5.0, 5.0))
        placed = model.state.document.active_features[-1].parameters
        assert (placed["x"], placed["y"], placed["z"]) == pytest.approx((5.0, 5.0, 5.0))

    def test_the_tree_does_not_spell_out_the_obvious_position(self):
        """Every row saying "at (0, 0, 20)" is noise on a line meant to read
        like a sentence."""
        model = scene()
        model.add_box(40, 40, 40)
        assert model.state.features[-1].label == "Add a 40 x 40 x 40 mm box"

    def test_it_still_says_where_a_shape_was_deliberately_put(self):
        model = scene()
        model.add_box(10, 10, 10, at=(5.0, 0.0, 30.0))
        assert "at (5, 0, 30)" in model.state.features[-1].label
