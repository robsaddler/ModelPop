"""Every capability exercised, so a missing library is a failing test.

Written the day repair failed in front of a user with ``No module named
'skimage'``. Nothing in ModelPop imports skimage; nothing declared it; every
import in the source resolved. ``trimesh.voxel.marching_cubes`` reaches for it
at the moment the last-resort repair runs, and no amount of checking imports
would ever have found that.

So these run the work rather than importing the module. They are deliberately
small - the point is to touch each dependency, not to test the behaviour, which
is done elsewhere.

``tools/check_dependencies.py`` is the same checks as a script, for asking the
question on a machine rather than in a test run.
"""

import numpy as np
import pytest

from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.readiness import assess
from modelpop.mesh import TrimeshIO, TrimeshOps

from .strategies import unit_cube


class TestTheMeshLibraries:
    def test_every_file_type_the_open_dialog_offers_round_trips(self, tmp_path):
        io = TrimeshIO()
        for suffix in (".stl", ".obj", ".ply", ".3mf", ".glb", ".off"):
            path = tmp_path / f"check{suffix}"
            assert io.save(unit_cube(10), path).ok, f"cannot write {suffix}"
            assert io.load(path).ok, f"cannot read {suffix}"

    def test_the_voxel_rebuild_runs(self):
        """The one that was missing a library.

        Called directly rather than through ``repair``: an easy repair never
        reaches it, which is exactly why nobody noticed it could not run.
        """
        rebuilt = TrimeshOps()._voxel_remesh(unit_cube(10))
        assert rebuilt.ok, f"the voxel rebuild failed: {rebuilt.detail}"

    def test_repairing_something_actually_broken_works(self):
        holed = Mesh(unit_cube(10).vertices, unit_cube(10).faces[2:])
        assert TrimeshOps().repair(holed).ok

    def test_decimation_runs(self):
        import trimesh

        sphere = trimesh.creation.icosphere(subdivisions=3)
        mesh = Mesh(np.asarray(sphere.vertices), np.asarray(sphere.faces, np.int32))
        reduced = TrimeshOps().decimate(mesh, 200)

        assert reduced.ok
        assert reduced.unwrap().triangle_count <= 200

    def test_booleans_run(self):
        other = Mesh(unit_cube(10).vertices + np.array([3.0, 0.0, 0.0]), unit_cube(10).faces)
        assert TrimeshOps().union(unit_cube(10), other).ok

    def test_the_readiness_checks_run(self):
        """They reach for ray casting and spatial trees, each its own library."""
        facts = TrimeshOps().inspect(unit_cube(10))
        assert assess(facts, PrinterProfile.p2s()) is not None

    def test_detail_rescue_is_constructable(self):
        from modelpop.mesh.detail_bake import TrimeshDetailBake

        assert TrimeshDetailBake() is not None


class TestWhatIsOptional:
    """Named, so "not installed" is a decision rather than a surprise."""

    def test_the_assistant_client_is_installed(self):
        """Optional in the packaging, but it is a button in the interface -
        and a button that raises ImportError is not an optional feature."""
        pytest.importorskip("anthropic")

    def test_the_printer_client_is_installed(self):
        pytest.importorskip("paho.mqtt.client")


class TestWeldingOnLoad:
    """A file read back must not come apart into loose triangles.

    STL stores three independent vertices per triangle and shares nothing, so
    without welding a sound model reads as rubble: a real 290,000 triangle
    model came back as 290,000 separate pieces with 871,000 holes, and repair
    escalated to a voxel rebuild to fix something that was not broken.
    """

    def test_a_cube_written_and_read_is_still_one_piece(self, tmp_path):
        io = TrimeshIO()
        path = tmp_path / "cube.stl"
        io.save(unit_cube(10), path)

        facts = TrimeshOps().inspect(io.load(path).unwrap())
        assert facts.shell_count == 1, f"it came back as {facts.shell_count} pieces"

    def test_it_comes_back_watertight(self, tmp_path):
        io = TrimeshIO()
        path = tmp_path / "cube.stl"
        io.save(unit_cube(10), path)

        assert TrimeshOps().inspect(io.load(path).unwrap()).is_watertight

    def test_the_vertices_are_shared_rather_than_repeated(self, tmp_path):
        """Eight corners, not twelve triangles times three."""
        io = TrimeshIO()
        path = tmp_path / "cube.stl"
        io.save(unit_cube(10), path)

        assert io.load(path).unwrap().vertex_count == 8

    def test_the_shape_is_unchanged_by_welding(self, tmp_path):
        """Lossless: it joins points that are already in the same place."""
        io = TrimeshIO()
        path = tmp_path / "cube.stl"
        io.save(unit_cube(10), path)
        back = io.load(path).unwrap()

        assert back.bounds.width.millimetres == pytest.approx(10.0, abs=1e-4)
        assert back.triangle_count == 12
