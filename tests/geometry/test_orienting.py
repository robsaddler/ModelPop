"""Choosing which way up a model should print.

Overhanging surface is what needs supports, and supports are what make a print
slow, wasteful and scarred where they are torn off.

The part worth testing hardest is not the search - it is the arithmetic that
turns "this direction should point at the bed" into turns the feature tree can
record. The vocabulary has rotation about one named axis at a time, so an
arbitrary orientation has to come back as up to three of them, and a decomposi-
tion that is subtly wrong produces a model lying at a plausible-looking angle
that is not the one that was chosen.
"""

import numpy as np
import pytest
import trimesh

from modelpop.domain.mesh import Mesh
from modelpop.domain.orienting import Resting, turns_that_put_down
from modelpop.mesh.orienting import best_resting_place


def turned_by(direction, turns) -> np.ndarray:
    """Apply the turns to a direction, the way the feature tree would.

    Through ``Mesh.turned``, so this measures what the application will
    actually do rather than a second implementation of it that agrees with the
    first because they share an author.
    """
    point = Mesh(
        np.array([[0.0, 0.0, 0.0], list(direction), [1.0, 1.0, 1.0]]),
        np.array([[0, 1, 2]], dtype=np.int32),
    )
    for turn in turns:
        point = point.turned(turn.degrees, turn.axis)
    moved = np.asarray(point.vertices[1])
    return moved / np.linalg.norm(moved)


class TestTurningADirectionDownwards:
    @pytest.mark.parametrize(
        "direction",
        [
            (0.0, 0.0, 1.0),
            (0.0, 0.0, -1.0),
            (1.0, 0.0, 0.0),
            (-1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, -1.0, 0.0),
            (0.577, 0.577, 0.577),
            (-0.3, 0.8, 0.52),
            (0.168, -0.176, 0.97),
            (0.0, 0.09, -0.996),
        ],
    )
    def test_it_ends_up_pointing_at_the_bed(self, direction):
        """The one property the whole decomposition exists for."""
        turns = turns_that_put_down(np.array(direction, dtype=np.float64))
        landed = turned_by(direction, turns)

        assert landed == pytest.approx([0.0, 0.0, -1.0], abs=1e-6), (
            f"{direction} ended up pointing {np.round(landed, 3).tolist()}"
        )

    def test_a_direction_already_down_needs_no_turns(self):
        assert turns_that_put_down(np.array([0.0, 0.0, -1.0])) == ()

    def test_every_turn_is_about_one_named_axis(self):
        """Because that is the only rotation the feature tree can record."""
        turns = turns_that_put_down(np.array([0.4, -0.5, 0.766]))

        assert turns, "an arbitrary direction produced no turns at all"
        assert all(turn.axis in "XYZ" for turn in turns)
        assert len(turns) <= 3

    def test_a_direction_of_nothing_is_refused_rather_than_guessed(self):
        assert turns_that_put_down(np.zeros(3)) == ()


class TestFindingTheBestWayUp:
    def a_tee(self) -> Mesh:
        """An upright T: a wide bar held up in the air on a thin stem.

        Printed as it stands, the whole underside of the bar overhangs. Laid on
        its back it is almost all flat, which is the answer a maker would give
        without thinking about it.
        """
        stem = trimesh.creation.box(extents=(6.0, 6.0, 40.0))
        stem.apply_translation([0.0, 0.0, 20.0])
        bar = trimesh.creation.box(extents=(40.0, 6.0, 6.0))
        bar.apply_translation([0.0, 0.0, 43.0])
        both = trimesh.util.concatenate([stem, bar])
        return Mesh(np.asarray(both.vertices), np.asarray(both.faces, np.int32))

    def test_it_finds_a_better_way_up_for_something_badly_placed(self):
        found = best_resting_place(self.a_tee())

        assert found is not None
        assert found.overhang_then < found.overhang_now, (
            f"it found nothing better than {found.overhang_now:.1%}"
        )
        assert found.is_worth_it

    def test_what_it_picks_can_actually_be_stood_on(self):
        """The whole reason steadiness is a gate and not a tie-break.

        On the dragon the least overhanging orientation of all balances it
        upside down on three hundredths of a square millimetre - 17.0% against
        20.9%, and it would fall over before the first layer finished.
        """
        found = best_resting_place(self.a_tee())

        assert found is not None
        assert found.is_steady, f"it chose a base of {found.steadiness:.0%}"

    def test_a_cube_is_already_the_best_way_up(self):
        box = trimesh.creation.box(extents=(20.0, 20.0, 20.0))
        found = best_resting_place(Mesh(np.asarray(box.vertices), np.asarray(box.faces, np.int32)))

        assert found is not None
        assert not found.is_worth_it
        assert found.overhang_now == pytest.approx(0.0, abs=1e-9)

    def test_an_empty_model_has_no_answer(self):
        assert best_resting_place(Mesh.empty()) is None


class TestSayingWhatItWouldDo:
    def test_a_model_already_the_right_way_up_says_so(self):
        said = Resting(down=(0.0, 0.0, -1.0), overhang_now=0.1, overhang_then=0.1).describe()

        assert "already lying the best way up" in said

    def test_a_gain_too_small_to_bother_with_says_so(self):
        """The dragon's case: a point of overhang is not worth moving it for."""
        from modelpop.domain.cad_commands import Rotate

        said = Resting(
            down=(0.0, 0.09, -0.996),
            overhang_now=0.209,
            overhang_then=0.199,
            steadiness=0.71,
            turns=(Rotate(354.8, "X"),),
        ).describe()

        assert "not worth moving it for" in said
        assert "21%" in said and "20%" in said

    def test_a_worthwhile_turn_names_the_numbers_and_the_turns(self):
        from modelpop.domain.cad_commands import Rotate

        found = Resting(
            down=(0.0, -1.0, 0.0),
            overhang_now=0.40,
            overhang_then=0.05,
            steadiness=0.9,
            turns=(Rotate(90.0, "X"),),
        )

        assert found.is_worth_it
        assert "40%" in found.describe()
        assert "5%" in found.describe()


class TestLayingItDownThroughTheTree:
    """The turns join the feature tree, so this undoes like anything else.

    There is deliberately no separate "auto-orient" state. A model turned this
    way is a model with a rotate and a move in its history, which reads back as
    a sentence, steps backwards, and rebuilds from nothing.
    """

    def session(self, mesh: Mesh):
        from modelpop.application.modelling import ModellingSession
        from modelpop.mesh import TrimeshIO, TrimeshOps

        made = ModellingSession(mesh_io=TrimeshIO(), mesh_ops=TrimeshOps())
        made.place(mesh, "a part", "body-1")
        return made

    def a_tee(self) -> Mesh:
        stem = trimesh.creation.box(extents=(6.0, 6.0, 40.0))
        stem.apply_translation([0.0, 0.0, 20.0])
        bar = trimesh.creation.box(extents=(40.0, 6.0, 6.0))
        bar.apply_translation([0.0, 0.0, 43.0])
        both = trimesh.util.concatenate([stem, bar])
        return Mesh(np.asarray(both.vertices), np.asarray(both.faces, np.int32))

    def test_it_actually_reduces_the_overhang(self):
        made = self.session(self.a_tee())
        before = best_resting_place(made.state.body("body-1").mesh)

        assert made.lay_it_down().ok

        after = best_resting_place(made.state.body("body-1").mesh)
        assert after.overhang_now < before.overhang_now, (
            f"overhang went {before.overhang_now:.1%} to {after.overhang_now:.1%}"
        )

    def test_it_comes_back_down_onto_the_plate(self):
        """Turning happens about the origin, so it has to be settled afterwards.

        Without this the model is left buried in the plate or hovering over it,
        which is the thing that was complained about the first time a rotation
        shipped: "you always sink them half way through the print plate".
        """
        made = self.session(self.a_tee())
        made.lay_it_down()

        assert made.state.body("body-1").bounds.min_z == pytest.approx(0.0, abs=1e-6)

    def test_every_step_is_in_the_tree(self):
        made = self.session(self.a_tee())
        made.lay_it_down()

        steps = [f.name for f in made.state.document.active_features]
        assert "rotate" in steps
        assert steps[-1] == "move", "it was not put back on the plate as a recorded step"

    def test_a_model_already_the_best_way_up_is_left_alone(self):
        box = trimesh.creation.box(extents=(20.0, 20.0, 20.0))
        made = self.session(Mesh(np.asarray(box.vertices), np.asarray(box.faces, np.int32)))
        before = [f.name for f in made.state.document.active_features]

        result = made.lay_it_down()

        assert not result.ok
        assert "already the best way up" in result.error
        assert [f.name for f in made.state.document.active_features] == before

    def test_it_says_so_when_nothing_is_selected(self):
        made = self.session(self.a_tee())
        made.select("")

        result = made.lay_it_down()

        assert not result.ok
        assert "Nothing is selected" in result.error

    def test_it_says_so_without_the_geometry_tools(self):
        from modelpop.application.modelling import ModellingSession
        from modelpop.mesh import TrimeshIO

        made = ModellingSession(mesh_io=TrimeshIO())
        made.place(self.a_tee(), "a part", "body-1")

        result = made.lay_it_down()

        assert not result.ok
        assert "unavailable" in result.error
