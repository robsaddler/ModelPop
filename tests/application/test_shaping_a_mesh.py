"""Shaping a model that arrived as triangles.

"I wanted starting points. You gave me the ability to get models from
Thingiverse, great, but I can't edit them? I need to be able to - why can't I
but the originator can?"

The originator kept the file it was built from. What Thingiverse hands over is
an STL: triangles, and nothing else - no faces, no edges, no history, no
parameters. That is not a shortcoming of this application, it is what the
format is, and it is the whole of why their copy is still editable and yours
is not.

It does not follow that nothing can be done to it, which is where this
application was genuinely at fault. Moving, turning and resizing are arithmetic
on points and always worked. **Hollowing and mirroring are arithmetic plus a
boolean**, and manifold3d is exact at those - they were refused for no better
reason than that nobody had written them.

What still cannot be done is anything that has to *name an edge*: filleting and
chamfering. Those are refused by name rather than silently dropped, because a
feature tree listing a step the geometry has not got is a tree that lies.
"""

import numpy as np
import pytest
import trimesh

from modelpop.application.modelling import ModellingSession
from modelpop.domain.cad_commands import Chamfer, Fillet, Hollow, Mirror, Plane
from modelpop.domain.mesh import Mesh
from modelpop.domain.units import Length
from modelpop.mesh import TrimeshIO, TrimeshOps


def as_downloaded(radius: float = 25.0) -> Mesh:
    """A model that arrived whole: triangles and nothing else."""
    blob = trimesh.creation.icosphere(subdivisions=3, radius=radius)
    blob.apply_translation([0.0, 0.0, radius])
    return Mesh(np.asarray(blob.vertices), np.asarray(blob.faces, np.int32))


def a_scene(mesh: Mesh | None = None) -> ModellingSession:
    session = ModellingSession(mesh_io=TrimeshIO(), mesh_ops=TrimeshOps())
    session.place(mesh if mesh is not None else as_downloaded(), "downloaded", "body-1")
    return session


class TestHollowingOne:
    """The single most useful thing to do to a downloaded model.

    They arrive solid all the way through, which is hours of print time and a
    spool of filament nobody needed to spend.
    """

    def test_most_of_the_filament_goes(self):
        session = a_scene()
        before = session.state.body("body-1").mesh.volume

        assert session.apply(Hollow(2.0), body="body-1").ok

        after = session.state.body("body-1").mesh.volume
        assert after < before * 0.5, f"only {1 - after / before:.0%} of the volume went"

    def test_the_outside_is_untouched(self):
        """A hollow nobody can see from the outside is the entire point."""
        session = a_scene()
        before = session.state.body("body-1").bounds

        session.apply(Hollow(2.0), body="body-1")

        after = session.state.body("body-1").bounds
        assert after.width.millimetres == pytest.approx(before.width.millimetres, abs=0.01)
        assert after.height.millimetres == pytest.approx(before.height.millimetres, abs=0.01)

    def test_it_is_still_a_solid(self):
        """Everything downstream - slicing especially - trusts this."""
        session = a_scene()
        session.apply(Hollow(2.0), body="body-1")

        facts = TrimeshOps().inspect(session.state.body("body-1").mesh, measure_walls=False)
        assert facts.is_watertight

    def test_it_joins_the_tree_and_undoes(self):
        session = a_scene()
        solid = session.state.body("body-1").mesh.volume

        session.apply(Hollow(2.0), body="body-1")
        assert [f.name for f in session.state.document.active_features] == [
            "place-mesh",
            "hollow",
        ]

        assert session.undo().ok
        assert session.state.body("body-1").mesh.volume == pytest.approx(solid)

    def test_a_wall_thicker_than_the_model_is_refused(self):
        """Not silently turned inside out, which is what the arithmetic does."""
        session = a_scene(as_downloaded(radius=3.0))

        outcome = session.apply(Hollow(20.0), body="body-1")

        assert not outcome.ok
        assert "too thin to hollow" in outcome.error or "could not be applied" in outcome.error


def off_to_one_side(radius: float = 25.0) -> Mesh:
    """A model that is not already symmetrical about the mirror plane.

    Mirroring a sphere centred on the plane maps it onto itself and proves
    nothing, which is what the first version of this test did.
    """
    blob = trimesh.creation.icosphere(subdivisions=3, radius=radius)
    blob.apply_translation([radius * 1.5, 0.0, radius])
    return Mesh(np.asarray(blob.vertices), np.asarray(blob.faces, np.int32))


class TestMirroringOne:
    def test_it_comes_back_wider(self):
        session = a_scene(off_to_one_side())
        before = session.state.body("body-1").bounds.width.millimetres

        assert session.apply(Mirror(Plane.YZ), body="body-1").ok

        after = session.state.body("body-1").bounds.width.millimetres
        assert after > before * 1.4, f"{before:.0f} mm became {after:.0f} mm"

    def test_the_copy_is_the_right_way_out(self):
        """Reflection reverses every triangle's winding. Left alone, the copy
        is a solid with its inside facing the world."""
        session = a_scene(off_to_one_side())
        session.apply(Mirror(Plane.YZ), body="body-1")

        assert session.state.body("body-1").mesh.volume > 0.0

    def test_it_joins_the_tree(self):
        session = a_scene(off_to_one_side())
        session.apply(Mirror(Plane.YZ), body="body-1")

        assert [f.name for f in session.state.document.active_features] == [
            "place-mesh",
            "mirror",
        ]


class TestWhatStillCannotBeDone:
    """Refused by name, and honestly about why.

    Filleting and chamfering have to name an edge. A mesh has no edges in that
    sense - every triangle boundary is an edge, and there are a million of
    them on a real download.
    """

    @pytest.mark.parametrize("command", [Fillet(2.0), Chamfer(2.0)])
    def test_the_edge_operations_are_refused(self, command):
        session = a_scene()

        outcome = session.apply(command, body="body-1")

        assert not outcome.ok

    def test_the_refusal_says_what_the_model_is_and_what_it_can_take(self):
        session = a_scene()

        outcome = session.apply(Fillet(2.0), body="body-1")

        assert "only triangles" in outcome.error
        assert "moved, turned, resized" in outcome.error

    def test_a_refusal_leaves_the_model_alone(self):
        session = a_scene()
        before = session.state.body("body-1").mesh.volume

        session.apply(Fillet(2.0), body="body-1")

        assert session.state.body("body-1").mesh.volume == pytest.approx(before)


class TestTheAdapterOnItsOwn:
    def test_thickening_runs_inwards_too(self):
        """Which is how hollowing gets its inner surface. Returning the mesh
        untouched for a negative distance made hollow subtract a model from
        itself and come back with nothing."""
        ops = TrimeshOps()
        mesh = as_downloaded()

        smaller = ops.thicken(mesh, Length.mm(-2.0))

        assert smaller.ok
        assert smaller.unwrap().volume < mesh.volume

    def test_mirroring_without_keeping_the_original_gives_one_half(self):
        ops = TrimeshOps()
        mesh = as_downloaded()

        flipped = ops.mirrored(mesh, "YZ", keep_original=False)

        assert flipped.ok
        assert flipped.unwrap().triangle_count == mesh.triangle_count

    def test_a_plane_it_does_not_know_is_refused(self):
        assert not TrimeshOps().mirrored(as_downloaded(), "sideways").ok


class TestCuttingAndJoiningShapes:
    """The operation that makes a downloaded model editable at all.

    Punch a hole through it, flatten a base off it, add a mounting boss to it.
    None of that needs a kernel or a history - it needs two closed surfaces and
    an exact boolean, and manifold3d is exactly that.

    The primitive is built as triangles by the mesh adapter rather than
    compiled through OCCT. That is not only faster, it is the only thing that
    would work: the other side of the boolean has no faces for a kernel to
    reason about. It also has to live in the adapter, because the application
    layer may not import a geometry library - the import-linter contract caught
    that and was right to.
    """

    def test_a_hole_can_be_drilled_through_it(self):
        from modelpop.domain.cad_commands import CreateCylinder

        session = a_scene()
        before = session.state.body("body-1").mesh.volume

        drilled = session.apply(CreateCylinder(6.0, 100.0, 0.0, 0.0, 25.0, cut=True), body="body-1")

        assert drilled.ok, drilled.error
        assert session.state.body("body-1").mesh.volume < before

    def test_what_is_left_is_still_a_solid(self):
        """A model with a hole in it still has to slice."""
        from modelpop.domain.cad_commands import CreateCylinder

        session = a_scene()
        session.apply(CreateCylinder(6.0, 100.0, 0.0, 0.0, 25.0, cut=True), body="body-1")

        facts = TrimeshOps().inspect(session.state.body("body-1").mesh, measure_walls=False)
        assert facts.is_watertight

    def test_a_shape_can_be_joined_onto_it(self):
        from modelpop.domain.cad_commands import CreateBox

        session = a_scene()
        before = session.state.body("body-1").mesh.volume

        joined = session.apply(CreateBox(12.0, 12.0, 40.0, 30.0, 0.0, 25.0), body="body-1")

        assert joined.ok, joined.error
        assert session.state.body("body-1").mesh.volume > before

    def test_both_join_the_tree_and_undo(self):
        from modelpop.domain.cad_commands import CreateCylinder

        session = a_scene()
        solid = session.state.body("body-1").mesh.volume

        session.apply(CreateCylinder(6.0, 100.0, 0.0, 0.0, 25.0, cut=True), body="body-1")
        assert [f.name for f in session.state.document.active_features] == [
            "place-mesh",
            "create-cylinder",
        ]

        assert session.undo().ok
        assert session.state.body("body-1").mesh.volume == pytest.approx(solid)

    def test_a_cut_that_would_take_everything_is_refused(self):
        """Not applied, leaving nothing on the plate and no way back."""
        from modelpop.domain.cad_commands import CreateBox

        session = a_scene()
        before = session.state.body("body-1").mesh.volume

        outcome = session.apply(CreateBox(500.0, 500.0, 500.0, cut=True), body="body-1")

        assert not outcome.ok
        assert session.state.body("body-1").mesh.volume == pytest.approx(before)

    def test_the_cutter_lands_where_the_command_says(self):
        """Same convention as the compiled path: built at the origin and moved.

        A hole 20 mm off to one side has to be 20 mm off to one side, or the
        same command means two different things depending on what it is cutting.
        """
        from modelpop.domain.cad_commands import CreateCylinder

        ops = TrimeshOps()
        drill = ops.solid_for(CreateCylinder(5.0, 40.0, 20.0, 0.0, 0.0, cut=True))

        centre = drill.bounds
        assert (centre.min_x + centre.max_x) / 2 == pytest.approx(20.0, abs=0.01)
        assert (centre.min_y + centre.max_y) / 2 == pytest.approx(0.0, abs=0.01)


class TestTheThreeWayChoice:
    """Where a new shape goes is asked, not guessed.

    Cutting and joining act on the selected object; a plain add makes a new
    one. A hidden rule that changed what a button did depending on what was
    selected would be worse than a third row on screen.
    """

    def view(self):
        from modelpop.presentation.modelling_view_model import ModellingViewModel

        made = ModellingViewModel(ModellingSession(mesh_io=TrimeshIO(), mesh_ops=TrimeshOps()))
        made.place_mesh(as_downloaded(), "downloaded")
        return made

    def test_cutting_acts_on_the_selected_object(self):
        made = self.view()
        before = made.selected_body.mesh.volume

        made.add_cylinder(6.0, 100.0, (0.0, 0.0, 25.0), cut=True)

        assert len(made.bodies) == 1
        assert made.selected_body.mesh.volume < before

    def test_joining_acts_on_the_selected_object(self):
        made = self.view()
        before = made.selected_body.mesh.volume

        made.add_box(12.0, 12.0, 40.0, (30.0, 0.0, 25.0), onto_the_selected=True)

        assert len(made.bodies) == 1
        assert made.selected_body.mesh.volume > before
