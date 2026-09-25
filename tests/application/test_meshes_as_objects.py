"""A model that arrived whole, as an object on the plate like any other.

This is the fix for the one that mattered. A model made from a photograph sat
there in plain sight and could not be selected, moved, dropped on the bed or
anything else - the object was visibly present and completely untouchable. It
had no feature tree, and everything built for a scene of objects asked the CAD
session and nothing else.

It now enters the tree as a single step saying where the geometry is, and the
steps after it are the ordinary Move, Rotate and ScaleTo. Rebuilding loads the
file and replays them with arithmetic - no kernel, measured at 40 ms for 82,000
triangles against two seconds for an OCCT rebuild.
"""

import numpy as np
import pytest

from modelpop.application.modelling import MESH_LIMIT, ModellingSession
from modelpop.domain.mesh import Mesh
from modelpop.domain.units import Length
from modelpop.mesh import TrimeshIO
from modelpop.presentation.modelling_view_model import ModellingViewModel, Outcome

from .test_modelling import FakeCompiler, cube


def scene() -> ModellingViewModel:
    """A session with a real mesh reader and *no kernel at all*.

    Deliberately: a model that arrived whole must be fully workable on a
    machine where build123d never loaded.
    """
    return ModellingViewModel(ModellingSession(None, None, TrimeshIO()))


def mixed() -> ModellingViewModel:
    """A session that can build parts as well as hold whole meshes."""
    return ModellingViewModel(ModellingSession(FakeCompiler(), None, TrimeshIO()))


class TestItBecomesAnObject:
    def test_placing_a_mesh_puts_an_object_on_the_plate(self):
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")

        assert len(model.bodies) == 1
        assert model.selected == model.bodies[0].id

    def test_it_is_named_after_where_it_came_from(self):
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")
        assert model.bodies[0].label == "Made from a picture"

    def test_it_works_with_no_cad_kernel_at_all(self):
        """The kernel is None here, and everything below still works."""
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")

        assert not model.can_build
        assert model.bodies

    def test_an_empty_mesh_is_not_placed(self):
        model = scene()
        model.place_mesh(Mesh.empty(), "Nothing")
        assert model.bodies == ()

    def test_the_geometry_survives_the_round_trip_through_a_file(self):
        """The document holds no geometry, so the mesh goes to disk and back."""
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")

        box = model.bodies[0].bounds
        assert box.width.millimetres == pytest.approx(40.0, abs=0.01)
        assert box.height.millimetres == pytest.approx(40.0, abs=0.01)


class TestItCanBeWorkedOn:
    def placed(self) -> ModellingViewModel:
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")
        return model

    def test_it_moves(self):
        model = self.placed()
        model.move(0, 0, 25)
        assert model.bodies[0].bounds.min_z == pytest.approx(25.0, abs=0.01)

    def test_it_turns(self):
        model = self.placed()
        before = model.bodies[0].bounds.width.millimetres
        model.rotate(90, "X")

        assert model.bodies[0].bounds.width.millimetres == pytest.approx(before, abs=0.01)
        assert len(model.state.document.active_features) == 2

    def test_it_resizes_to_a_stated_height(self):
        model = self.placed()
        model.scale_selected_to(Length.mm(80))
        assert model.bodies[0].bounds.height.millimetres == pytest.approx(80.0, abs=0.01)

    def test_it_resizes_by_a_proportion(self):
        model = self.placed()
        model.scale_selected_by(0.5)
        assert model.bodies[0].bounds.height.millimetres == pytest.approx(20.0, abs=0.01)

    def test_every_step_undoes(self):
        model = self.placed()
        model.move(0, 0, 25)
        model.undo()
        assert model.bodies[0].bounds.min_z == pytest.approx(0.0, abs=0.01)

    def test_it_can_be_deleted(self):
        model = self.placed()
        model.delete_selected()
        assert model.bodies == ()

    def test_it_can_be_copied(self):
        model = self.placed()
        model.duplicate_selected()

        assert len(model.bodies) == 2
        first, copy = model.bodies
        assert copy.bounds.min_x != first.bounds.min_x

    def test_it_can_be_renamed(self):
        model = self.placed()
        model.rename_selected("The dragon")
        assert model.bodies[0].label == "The dragon"


class TestWhatItCannotDo:
    """Honest about the limit, rather than failing obscurely."""

    def placed(self) -> tuple[ModellingViewModel, list[Outcome]]:
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")
        outcomes: list[Outcome] = []
        model.on_outcome(outcomes.append)
        return model, outcomes

    @pytest.mark.parametrize(
        "change",
        [lambda m: m.fillet(2), lambda m: m.chamfer(2), lambda m: m.hollow(1.5)],
        ids=["round", "chamfer", "hollow"],
    )
    def test_a_shape_operation_is_refused_with_a_reason(self, change):
        model, outcomes = self.placed()
        change(model)

        assert outcomes[-1].refused
        assert "arrived whole" in outcomes[-1].detail

    def test_the_reason_says_what_can_be_done_instead(self):
        assert "moved, turned, resized" in MESH_LIMIT

    def test_a_refused_change_leaves_the_object_alone(self):
        model, _outcomes = self.placed()
        model.fillet(2)

        assert len(model.state.document.active_features) == 1
        assert model.bodies[0].bounds.width.millimetres == pytest.approx(40.0, abs=0.01)


class TestReworkingIt:
    """Repair and simplify rewrite triangles rather than adding a step."""

    def reworked(self) -> ModellingViewModel:
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")
        model.rework_selected(cube(10), "Repair it")
        return model

    def test_it_replaces_the_geometry_in_place(self):
        model = self.reworked()

        assert len(model.bodies) == 1, "it arrived as a second object beside the first"
        assert model.bodies[0].bounds.width.millimetres == pytest.approx(10.0, abs=0.01)

    def test_it_keeps_the_name(self):
        assert self.reworked().bodies[0].label == "Made from a picture"

    def test_it_undoes(self):
        model = self.reworked()
        model.undo()
        assert model.bodies[0].bounds.width.millimetres == pytest.approx(40.0, abs=0.01)

    def test_knowing_which_kind_of_object_is_in_hand(self):
        model = scene()
        model.place_mesh(cube(40), "Made from a picture")
        assert model.is_a_whole_mesh


class TestAMixedScene:
    """A built part and a model from a picture, on one plate."""

    def both(self) -> ModellingViewModel:
        model = mixed()
        model.add_box(20, 20, 20)
        model.place_mesh(cube(40), "Made from a picture")
        return model

    def test_both_kinds_stand_together(self):
        model = self.both()

        assert len(model.bodies) == 2
        assert [b.label for b in model.bodies] == ["Box", "Made from a picture"]

    def test_moving_the_mesh_leaves_the_built_part_alone(self):
        model = self.both()
        before = model.bodies[0].bounds.min_z

        model.move(0, 0, 30)

        assert model.bodies[0].bounds.min_z == pytest.approx(before)
        assert model.bodies[1].bounds.min_z == pytest.approx(30.0, abs=0.01)

    def test_the_built_part_can_still_be_rounded(self):
        """The limit belongs to the mesh, not to the scene it is standing in."""
        model = self.both()
        model.select("body-1")
        model.fillet(1)

        assert len(model.state.document.features_for("body-1")) == 2

    def test_the_whole_scene_is_one_mesh_for_the_slicer(self):
        model = self.both()

        whole = model.state.mesh
        assert whole is not None
        assert whole.triangle_count == sum(b.mesh.triangle_count for b in model.bodies)


class TestAModelThatArrivesWithNoScale:
    """A mesh from a picture carries no units at all.

    Measured on a real generation: the file came in one millimetre across,
    which on a 256 mm plate is invisible and makes every measurement taken off
    it meaningless. Only the implausible extremes are touched.
    """

    def tiny(self) -> Mesh:
        return Mesh(cube(40).vertices * 0.025, cube(40).faces)

    def test_a_model_a_millimetre_across_is_given_a_workable_size(self):
        model = scene()
        assert self.tiny().bounds.largest_dimension.millimetres == pytest.approx(1.0)

        model.place_mesh(self.tiny(), "Made from a picture")
        assert model.bodies[0].bounds.largest_dimension.millimetres == pytest.approx(60.0, abs=0.1)

    def test_it_says_that_it_chose_the_size(self):
        """Never silently: the real size is the user's to set."""
        model = scene()
        model.place_mesh(self.tiny(), "Made from a picture")
        assert "sized to fit" in model.bodies[0].label

    def test_it_keeps_the_proportions(self):
        model = scene()
        squashed = Mesh(cube(40).vertices * np.array([0.025, 0.0125, 0.025]), cube(40).faces)
        model.place_mesh(squashed, "Made from a picture")

        box = model.bodies[0].bounds
        assert box.depth.millimetres == pytest.approx(box.width.millimetres / 2, rel=0.01)

    def test_a_model_of_a_sensible_size_is_left_exactly_alone(self):
        """A part deliberately 40 mm must not be resized behind anybody's back."""
        model = scene()
        model.place_mesh(cube(40), "Opened")

        assert model.bodies[0].bounds.largest_dimension.millimetres == pytest.approx(40.0, abs=0.01)
        assert model.bodies[0].label == "Opened"

    def test_a_model_a_few_millimetres_across_is_given_a_size_too(self):
        """What a real Thingiverse download measured: 7.2 mm, 1.1M triangles."""
        model = scene()
        model.place_mesh(Mesh(cube(40).vertices * 0.18, cube(40).faces), "Downloaded")
        assert model.bodies[0].bounds.largest_dimension.millimetres == pytest.approx(60.0, abs=0.1)

    def test_a_model_far_bigger_than_the_machine_is_brought_down(self):
        model = scene()
        model.place_mesh(cube(4000), "Opened")
        assert model.bodies[0].bounds.largest_dimension.millimetres == pytest.approx(60.0, abs=0.1)


class TestItArrivesOnThePlate:
    def test_a_model_lands_on_the_bed_rather_than_through_it(self):
        """Reported as the application sinking every model it was given."""
        model = scene()
        sunk = Mesh(cube(40).vertices - np.array([0.0, 0.0, 20.0]), cube(40).faces)
        assert sunk.bounds.min_z == pytest.approx(-20.0)

        model.place_mesh(sunk, "Made from a picture")
        assert model.bodies[0].bounds.min_z == pytest.approx(0.0, abs=0.01)

    def test_a_model_floating_above_the_bed_is_brought_down_too(self):
        model = scene()
        floating = Mesh(cube(40).vertices + np.array([0.0, 0.0, 90.0]), cube(40).faces)
        model.place_mesh(floating, "Made from a picture")
        assert model.bodies[0].bounds.min_z == pytest.approx(0.0, abs=0.01)
