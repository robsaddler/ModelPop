"""Baking a colour texture into the surface it describes.

The failure this fixes is the one every consumer AI-3D tool shares: the detail
lives in a texture, the slicer cannot see colour, and the print is a smooth
blob. What these check is that the relief lands where it was asked to, that the
model does not tear at the texture's seam, and that a model with nothing to bake
is refused in words rather than silently returned unchanged.
"""

import numpy as np
import pytest
import trimesh

from modelpop.mesh.detail_bake import TrimeshDetailBake, displace, luminance_at

RADIUS = 30.0


def uv_for(vertices: np.ndarray, radius: float = RADIUS) -> np.ndarray:
    """Spherical texture coordinates, the way a generator would supply them."""
    points = vertices / radius
    u = 0.5 + np.arctan2(points[:, 2], points[:, 0]) / (2 * np.pi)
    v = 0.5 - np.arcsin(np.clip(points[:, 1], -1.0, 1.0)) / np.pi
    return np.column_stack([u, v])


def striped(size: int = 256, stripes: int = 24) -> np.ndarray:
    across = np.linspace(0, 1, size, endpoint=False)
    grid = np.sin(2 * np.pi * stripes * across)[None, :] * np.ones((size, 1))
    return ((grid * 0.5 + 0.5) * 255).astype(np.uint8)


@pytest.fixture
def textured_model(tmp_path):
    """A real textured GLB, written to disk, like the generator's own output."""
    from PIL import Image

    sphere = trimesh.creation.icosphere(subdivisions=3, radius=RADIUS)
    image = Image.fromarray(striped()).convert("RGB")
    sphere.visual = trimesh.visual.TextureVisuals(
        uv=uv_for(np.asarray(sphere.vertices)),
        material=trimesh.visual.material.PBRMaterial(baseColorTexture=image),
    )
    path = tmp_path / "textured.glb"
    sphere.export(path)
    return path


class TestReadingATexture:
    def test_brightness_comes_back_between_nothing_and_one(self):
        uv = np.array([[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]])
        found = luminance_at(striped().astype(np.float64), uv)

        assert found.min() >= 0.0
        assert found.max() <= 1.0

    def test_white_reads_as_bright_and_black_as_dark(self):
        texture = np.zeros((8, 8, 3))
        texture[0, 0] = 255.0
        uv = np.array([[0.01, 0.99], [0.5, 0.5]])
        found = luminance_at(texture, uv)

        assert found[0] > 0.9
        assert found[1] < 0.1

    def test_green_reads_brighter_than_blue_because_eyes_say_so(self):
        """A flat average makes a green ridge and a blue one the same depth."""
        green = luminance_at(np.full((4, 4, 3), [0, 255, 0], dtype=float), np.array([[0.5, 0.5]]))
        blue = luminance_at(np.full((4, 4, 3), [0, 0, 255], dtype=float), np.array([[0.5, 0.5]]))

        assert green[0] > blue[0]

    def test_a_greyscale_texture_needs_no_channels(self):
        found = luminance_at(np.full((4, 4), 0.5), np.array([[0.5, 0.5]]))
        assert found[0] == pytest.approx(0.5)

    def test_a_coordinate_past_the_edge_wraps_rather_than_smearing(self):
        """A texture repeats; clamping would drag one row across the whole seam."""
        texture = striped().astype(np.float64)
        inside = luminance_at(texture, np.array([[0.25, 0.5]]))
        wrapped = luminance_at(texture, np.array([[1.25, 0.5]]))

        assert inside[0] == pytest.approx(wrapped[0])


class TestPushingTheSurfaceAbout:
    def test_the_deepest_point_moves_exactly_as_far_as_was_asked(self):
        vertices = np.zeros((3, 3))
        normals = np.array([[0.0, 0.0, 1.0]] * 3)
        moved = displace(vertices, normals, np.array([0.0, 0.5, 1.0]), 0.6)

        assert np.abs(moved[:, 2]).max() == pytest.approx(0.6)

    def test_it_moves_in_and_out_rather_than_only_outwards(self):
        """A bake that only pushes out inflates the model, and the size the user
        asked for quietly stops being the size they get."""
        vertices = np.zeros((4, 3))
        normals = np.array([[0.0, 0.0, 1.0]] * 4)
        moved = displace(vertices, normals, np.array([0.0, 0.3, 0.7, 1.0]), 1.0)

        assert moved[:, 2].min() < 0
        assert moved[:, 2].max() > 0

    def test_a_flat_texture_moves_nothing(self):
        vertices = np.zeros((3, 3))
        normals = np.array([[0.0, 0.0, 1.0]] * 3)
        moved = displace(vertices, normals, np.array([0.5, 0.5, 0.5]), 0.6)

        assert np.allclose(moved, vertices)


class TestBakingAWholeModel:
    def test_a_textured_model_comes_back_with_relief_on_it(self, textured_model):
        baked = TrimeshDetailBake().rescue(textured_model, depth_mm=0.6)

        assert baked.ok, getattr(baked, "error", "")
        radii = np.linalg.norm(baked.unwrap().vertices, axis=1)
        assert radii.max() - radii.min() > 0.5, "the surface came back smooth"

    def test_the_relief_is_about_the_depth_that_was_asked_for(self, textured_model):
        shallow = TrimeshDetailBake().rescue(textured_model, depth_mm=0.2).unwrap()
        deep = TrimeshDetailBake().rescue(textured_model, depth_mm=0.8).unwrap()

        def spread(mesh):
            radii = np.linalg.norm(mesh.vertices, axis=1)
            return float(radii.max() - radii.min())

        assert spread(deep) > spread(shallow) * 2

    def test_the_model_does_not_tear_at_the_texture_seam(self, textured_model):
        """Where u returns to zero is the obvious place for a mesh to split."""
        baked = TrimeshDetailBake().rescue(textured_model, depth_mm=0.6).unwrap()
        body = trimesh.Trimesh(baked.vertices, baked.faces, process=False)

        assert body.is_watertight
        assert body.is_winding_consistent

    def test_the_mesh_is_made_dense_enough_to_hold_the_detail(self, textured_model):
        """A feature needs two vertices across it, and a generated mesh has far
        too few - so the geometry has to grow before anything is baked into it."""
        source = trimesh.load(textured_model, force="mesh", process=False)
        baked = TrimeshDetailBake().rescue(textured_model, depth_mm=0.4).unwrap()

        assert baked.triangle_count > len(source.faces)

    def test_an_untextured_model_is_refused_in_words(self, tmp_path):
        """Which includes everything drawn with the CAD tools."""
        plain = tmp_path / "plain.stl"
        trimesh.creation.box(extents=(20, 20, 20)).export(plain)

        baked = TrimeshDetailBake().rescue(plain)

        assert not baked.ok
        assert "no texture to bake" in baked.error

    def test_a_file_that_is_not_a_model_is_refused_rather_than_raising(self, tmp_path):
        rubbish = tmp_path / "notes.txt"
        rubbish.write_text("this is not a model", encoding="utf-8")

        assert not TrimeshDetailBake().rescue(rubbish).ok

    def test_a_missing_file_is_refused_rather_than_raising(self, tmp_path):
        assert not TrimeshDetailBake().rescue(tmp_path / "gone.glb").ok

    def test_relief_that_would_swallow_the_model_is_refused(self, textured_model):
        baked = TrimeshDetailBake().rescue(textured_model, depth_mm=40.0)

        assert not baked.ok
        assert "swallow" in baked.error
