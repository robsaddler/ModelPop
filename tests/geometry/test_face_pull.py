"""Taking hold of a face - SketchUp's push/pull.

The operation people mean when they say they want to model rather than to
configure something. Everything else in this vocabulary makes a shape out of
numbers; this one changes a shape that is already there by pointing at part of
it and pulling.

What is tested hardest here is which triangles count as "the face you are
pointing at". A flat surface on a tessellated solid is many triangles, and
getting that set wrong means the highlight says one thing and the pull does
another - which is worse than no highlight at all.
"""

import numpy as np
import pytest
import trimesh

from modelpop.domain.cad_commands import CreateBox, PushPull
from modelpop.domain.commands import Document
from modelpop.rendering.face_pull import coplanar_with


def a_box(wide: float = 40.0, deep: float = 30.0, tall: float = 20.0):
    box = trimesh.creation.box(extents=(wide, deep, tall))
    return np.asarray(box.vertices, dtype=np.float64), np.asarray(box.faces)


def the_triangle_facing(faces, vertices, direction) -> int:
    """Any triangle whose outward normal points the given way."""
    wanted = np.array(direction, dtype=np.float64)
    corners = vertices[faces]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    return int(np.argmax(normals @ wanted))


class TestWhichTrianglesAreTheSameFace:
    def test_a_box_face_is_both_of_its_triangles(self):
        """A cube face is two triangles, and grabbing one means both."""
        vertices, faces = a_box()
        top = the_triangle_facing(faces, vertices, (0, 0, 1))

        on_it, normal = coplanar_with(top, vertices, faces)

        assert len(on_it) == 2
        assert normal == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)

    def test_the_opposite_face_is_not_part_of_it(self):
        """Parallel is not the same face - a plate has two sides."""
        vertices, faces = a_box()
        top = the_triangle_facing(faces, vertices, (0, 0, 1))
        bottom = the_triangle_facing(faces, vertices, (0, 0, -1))

        on_it, _ = coplanar_with(top, vertices, faces)

        assert bottom not in on_it

    def test_a_face_at_right_angles_is_not_part_of_it(self):
        vertices, faces = a_box()
        top = the_triangle_facing(faces, vertices, (0, 0, 1))
        side = the_triangle_facing(faces, vertices, (1, 0, 0))

        on_it, _ = coplanar_with(top, vertices, faces)

        assert side not in on_it

    def test_the_flat_end_of_a_cylinder_comes_whole(self):
        """Many triangles, one face - and the curved side is not in it."""
        tube = trimesh.creation.cylinder(radius=10.0, height=20.0, sections=64)
        vertices = np.asarray(tube.vertices, dtype=np.float64)
        faces = np.asarray(tube.faces)
        top = the_triangle_facing(faces, vertices, (0, 0, 1))

        on_it, normal = coplanar_with(top, vertices, faces)

        assert normal == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)
        assert len(on_it) >= 32, f"only {len(on_it)} triangles of the end cap were found"
        highest = vertices[faces[on_it]][:, :, 2]
        assert np.allclose(highest, 10.0, atol=1e-6), "it reached past the flat end"

    def test_the_curved_side_of_a_cylinder_is_not_one_face(self):
        """Curved means every triangle points somewhere slightly different."""
        tube = trimesh.creation.cylinder(radius=10.0, height=20.0, sections=64)
        vertices = np.asarray(tube.vertices, dtype=np.float64)
        faces = np.asarray(tube.faces)
        side = the_triangle_facing(faces, vertices, (1, 0, 0))

        on_it, _ = coplanar_with(side, vertices, faces)

        assert len(on_it) < 8, f"{len(on_it)} triangles of a curved wall read as one flat face"

    def test_two_faces_of_a_thin_plate_stay_apart(self):
        """The case the offset test exists for: 1 mm apart and parallel."""
        vertices, faces = a_box(40.0, 40.0, 1.0)
        top = the_triangle_facing(faces, vertices, (0, 0, 1))

        on_it, _ = coplanar_with(top, vertices, faces)
        heights = vertices[faces[on_it]][:, :, 2]

        assert np.allclose(heights, 0.5, atol=1e-6), "it took in the far side of the plate"


class TestTheCommand:
    def test_pulling_and_pushing_read_differently(self):
        assert "Pull" in PushPull((0, 0, 10), 5.0).describe()
        assert "Push" in PushPull((0, 0, 10), -5.0).describe()

    def test_it_says_where_and_how_far(self):
        said = PushPull((0.0, 0.0, 10.0), -2.5).describe()

        assert "(0, 0, 10)" in said
        assert "2.5 mm" in said

    def test_a_twitch_does_nothing(self):
        """A slipped hand must not fill the tree with steps nobody can see."""
        assert not PushPull((0, 0, 10), 0.001).does_anything
        assert PushPull((0, 0, 10), 0.5).does_anything

    def test_it_survives_being_written_down_and_read_back(self):
        from modelpop.domain.cad_commands import command_from

        original = PushPull((1.5, -2.0, 10.0), -3.25)
        document = original.apply(Document())
        again = command_from(document.active_features[-1])

        assert again == original

    def test_it_joins_the_tree_like_anything_else(self):
        document = CreateBox(40, 30, 20).apply(Document())
        document = PushPull((0.0, 0.0, 10.0), 10.0).apply(document)

        assert [f.name for f in document.active_features] == ["create-box", "push-pull"]

    def test_a_point_beyond_anything_printable_is_clamped(self):
        """These arrive from a language model as readily as from a mouse."""
        pulled = PushPull((1e9, 0.0, 0.0), 1e9)

        assert abs(pulled.at[0]) <= 1000.0
        assert abs(pulled.distance) <= 1000.0
