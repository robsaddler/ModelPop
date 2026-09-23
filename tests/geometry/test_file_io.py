"""Reading and writing mesh files.

The file boundary is where users hand us whatever they downloaded, and a fair
amount of it is broken. Nothing here may raise; a bad file is an expected
outcome and belongs in the Result.
"""

import numpy as np
import pytest
import trimesh

from modelpop.domain import Length, Mesh, Unit
from modelpop.mesh import TrimeshIO


@pytest.fixture(scope="module")
def io() -> TrimeshIO:
    return TrimeshIO()


@pytest.fixture
def cube() -> Mesh:
    body = trimesh.creation.box(extents=(20, 20, 20))
    return Mesh(np.asarray(body.vertices), np.asarray(body.faces, np.int32))


class TestRoundTrip:
    @pytest.mark.parametrize("suffix", [".stl", ".obj", ".ply", ".glb", ".off"])
    def test_a_mesh_survives_a_round_trip(self, io, cube, tmp_path, suffix):
        path = tmp_path / f"cube{suffix}"
        assert io.save(cube, path).ok

        result = io.load(path)
        assert result.ok, getattr(result, "error", "")
        loaded = result.unwrap()
        assert loaded.volume == pytest.approx(cube.volume, rel=1e-4)
        assert loaded.triangle_count == cube.triangle_count

    def test_3mf_round_trips(self, io, cube, tmp_path):
        path = tmp_path / "cube.3mf"
        assert io.save(cube, path).ok
        loaded = io.load(path).unwrap()
        assert loaded.volume == pytest.approx(cube.volume, rel=1e-4)

    def test_saving_creates_missing_directories(self, io, cube, tmp_path):
        path = tmp_path / "deep" / "nested" / "cube.stl"
        assert io.save(cube, path).ok
        assert path.exists()


class TestUnits:
    def test_a_file_without_units_is_assumed_to_be_millimetres(self, io, cube, tmp_path):
        # STL and OBJ carry no units; millimetres is what every slicer assumes
        path = tmp_path / "cube.stl"
        io.save(cube, path)
        assert io.load(path).unwrap().unit is Unit.MILLIMETRE

    def test_a_mesh_in_inches_is_written_in_millimetres(self, io, tmp_path):
        """A file that lies about its scale is worse than useless."""
        body = trimesh.creation.box(extents=(1, 1, 1))
        one_inch_cube = Mesh(np.asarray(body.vertices), np.asarray(body.faces, np.int32), Unit.INCH)
        path = tmp_path / "inch.stl"
        assert io.save(one_inch_cube, path).ok

        loaded = io.load(path).unwrap()
        assert loaded.bounds.width.millimetres == pytest.approx(25.4, rel=1e-4)
        # STL stores coordinates as float32, so an exact comparison would fail on
        # 25.399999618530273. Tolerance here is a property of the format, not slack.
        assert loaded.bounds.width.is_close(Length.inches(1), Length.mm(1e-3))


class TestBrokenInput:
    """Every one of these must come back as a Failure, never an exception."""

    def test_a_missing_file(self, io, tmp_path):
        result = io.load(tmp_path / "nope.stl")
        assert not result.ok
        assert "not found" in result.error

    def test_an_empty_file(self, io, tmp_path):
        path = tmp_path / "empty.stl"
        path.write_bytes(b"")
        result = io.load(path)
        assert not result.ok
        assert "empty" in result.error

    def test_a_file_full_of_nonsense(self, io, tmp_path):
        path = tmp_path / "junk.stl"
        path.write_bytes(b"this is not a mesh, it is a disappointment")
        assert not io.load(path).ok

    def test_a_truncated_binary_stl(self, io, cube, tmp_path):
        good = tmp_path / "good.stl"
        io.save(cube, good)
        truncated = tmp_path / "truncated.stl"
        truncated.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 3])
        # either a clean failure or a partial mesh; what matters is it does not raise
        io.load(truncated)

    def test_an_unsupported_suffix(self, io, tmp_path):
        path = tmp_path / "model.docx"
        path.write_bytes(b"not a mesh")
        result = io.load(path)
        assert not result.ok
        assert "Unsupported" in result.error

    def test_a_file_with_no_suffix(self, io, tmp_path):
        path = tmp_path / "model"
        path.write_bytes(b"x")
        assert not io.load(path).ok

    def test_saving_an_empty_mesh_is_refused(self, io, tmp_path):
        result = io.save(Mesh.empty(), tmp_path / "empty.stl")
        assert not result.ok
        assert "Nothing to save" in result.error

    def test_saving_to_an_unsupported_suffix_is_refused(self, io, cube, tmp_path):
        assert not io.save(cube, tmp_path / "cube.docx").ok


class TestScenes:
    def test_a_multi_object_file_loads_as_one_mesh(self, io, tmp_path):
        """A user dragging in a multi-part model expects to see all of it."""
        a = trimesh.creation.box(extents=(10, 10, 10))
        b = trimesh.creation.box(extents=(10, 10, 10))
        b.apply_translation((30, 0, 0))
        scene = trimesh.Scene([a, b])
        path = tmp_path / "two.glb"
        path.write_bytes(scene.export(file_type="glb"))

        loaded = io.load(path).unwrap()
        assert loaded.volume == pytest.approx(2000.0, rel=1e-3)


class TestCapabilities:
    def test_advertises_the_formats_it_can_read(self, io):
        suffixes = io.supported_suffixes()
        assert ".stl" in suffixes
        assert ".3mf" in suffixes

    def test_every_advertised_suffix_starts_with_a_dot(self, io):
        assert all(s.startswith(".") for s in io.supported_suffixes())

    def test_every_advertised_suffix_is_lower_case(self, io):
        assert all(s == s.lower() for s in io.supported_suffixes())
