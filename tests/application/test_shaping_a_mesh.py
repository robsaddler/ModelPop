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
