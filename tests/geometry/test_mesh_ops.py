import numpy as np
import pytest
import trimesh
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from modelpop.domain import Mesh, Unit
from modelpop.mesh import TrimeshOps, thinnest_wall

# Geometry work is slow enough that hypothesis's default deadline is unhelpful here.
geometry_settings = settings(
    deadline=None, max_examples=25, suppress_health_check=[HealthCheck.too_slow]
)


@pytest.fixture(scope="module")
def ops() -> TrimeshOps:
    return TrimeshOps()


def box(size: float = 20.0) -> Mesh:
    body = trimesh.creation.box(extents=(size, size, size))
    return Mesh(np.asarray(body.vertices), np.asarray(body.faces, np.int32))


def sphere(radius: float = 8.0) -> Mesh:
    body = trimesh.creation.icosphere(subdivisions=3, radius=radius)
    return Mesh(np.asarray(body.vertices), np.asarray(body.faces, np.int32))


def box_with_hole(size: float = 20.0, removed: int = 2) -> Mesh:
    body = trimesh.creation.box(extents=(size, size, size))
    return Mesh(np.asarray(body.vertices), np.asarray(body.faces[:-removed], np.int32))


class TestInspection:
    def test_recognises_a_healthy_solid(self, ops):
        facts = ops.inspect(box())
        assert facts.is_watertight
        assert facts.is_winding_consistent
        assert facts.hole_count == 0
        assert facts.shell_count == 1

    def test_recognises_an_open_mesh_and_counts_its_open_edges(self, ops):
        facts = ops.inspect(box_with_hole())
        assert not facts.is_watertight
        assert facts.hole_count > 0

    def test_counts_separate_shells(self, ops):
        a = trimesh.creation.box(extents=(5, 5, 5))
        b = trimesh.creation.box(extents=(5, 5, 5))
        b.apply_translation((50, 0, 0))
        both = trimesh.util.concatenate([a, b])
        facts = ops.inspect(Mesh(np.asarray(both.vertices), np.asarray(both.faces, np.int32)))
        assert facts.shell_count == 2

    def test_an_empty_mesh_inspects_without_exploding(self, ops):
        facts = ops.inspect(Mesh.empty())
        assert not facts.is_watertight
        assert facts.hole_count == 0

    def test_measures_bed_contact_area(self, ops):
        # a 20mm cube resting on the bed has a 400 mm^2 footprint
        facts = ops.inspect(box(20).dropped_to_bed())
        assert facts.bed_contact_area_mm2 == pytest.approx(400.0, rel=0.01)

    def test_wall_measurement_can_be_skipped_for_speed(self, ops):
        assert ops.inspect(box(), measure_walls=False).thinnest_wall is None


class TestRepair:
    def test_repairs_an_open_mesh_into_a_closed_one(self, ops):
        result = ops.repair(box_with_hole())
        assert result.ok, getattr(result, "error", "")
        repaired = result.unwrap()
        assert ops.inspect(repaired).is_watertight

    def test_repair_preserves_the_volume_it_was_meant_to_restore(self, ops):
        repaired = ops.repair(box_with_hole(20.0)).unwrap()
        assert repaired.volume == pytest.approx(8000.0, rel=1e-6)

    def test_repairing_a_healthy_mesh_is_near_enough_a_no_op(self, ops):
        original = box()
        repaired = ops.repair(original).unwrap()
        assert repaired.volume == pytest.approx(original.volume, rel=1e-6)

    def test_reports_failure_rather_than_returning_a_broken_mesh(self, ops):
        """The single worst thing repair can do is claim success while broken."""
        result = ops.repair(Mesh.empty())
        assert not result.ok
        assert "no geometry" in result.error

    def test_repair_never_claims_success_without_being_watertight(self, ops):
        for candidate in (box_with_hole(20, 2), box_with_hole(20, 6), box(), sphere()):
            result = ops.repair(candidate)
            if result.ok:
                assert ops.inspect(result.unwrap()).is_watertight, (
                    "repair reported success but left the mesh open"
                )


class TestNormalise:
    def test_removes_duplicate_vertices(self, ops):
        body = trimesh.creation.box(extents=(10, 10, 10))
        doubled = np.vstack([body.vertices, body.vertices])
        faces = np.vstack([body.faces, body.faces + len(body.vertices)])
        messy = Mesh(doubled, faces.astype(np.int32))
        cleaned = ops.normalise(messy)
        assert cleaned.vertex_count < messy.vertex_count

    def test_leaves_a_clean_mesh_alone(self, ops):
        clean = box()
        assert ops.normalise(clean).volume == pytest.approx(clean.volume, rel=1e-9)

    def test_an_empty_mesh_survives(self, ops):
        assert ops.normalise(Mesh.empty()).is_empty


class TestBooleans:
    def test_subtracting_a_fully_enclosed_sphere_removes_its_volume(self, ops):
        result = ops.difference(box(20), sphere(8))
        assert result.ok
        # an icosphere is slightly smaller than a true sphere, hence the loose tolerance
        assert result.unwrap().volume == pytest.approx(8000 - 2144.7, rel=0.02)

    def test_union_with_an_enclosed_shape_changes_nothing(self, ops):
        result = ops.union(box(20), sphere(8))
        assert result.ok
        assert result.unwrap().volume == pytest.approx(8000.0, rel=1e-3)

    def test_intersection_with_an_enclosed_shape_is_that_shape(self, ops):
        result = ops.intersection(box(20), sphere(8))
        assert result.ok
        assert result.unwrap().volume == pytest.approx(sphere(8).volume, rel=1e-3)

    def test_an_empty_operand_fails_rather_than_raising(self, ops):
        result = ops.difference(box(), Mesh.empty())
        assert not result.ok
        assert "empty" in result.error

    def test_mismatched_units_are_reconciled_not_rejected(self, ops):
        # a 20mm box minus a sphere described in centimetres
        in_cm = sphere(0.8).with_unit(Unit.CENTIMETRE)
        result = ops.difference(box(20), in_cm)
        assert result.ok
        assert result.unwrap().volume == pytest.approx(8000 - 2144.7, rel=0.02)

    @geometry_settings
    @given(st.floats(min_value=2.0, max_value=9.0))
    def test_the_union_of_two_watertight_solids_is_watertight(self, radius):
        """The property that matters most: booleans must not produce broken geometry."""
        ops = TrimeshOps()
        result = ops.union(box(20), sphere(radius))
        assert result.ok
        assert ops.inspect(result.unwrap(), measure_walls=False).is_watertight

    @geometry_settings
    @given(st.floats(min_value=2.0, max_value=9.0))
    def test_union_volume_never_exceeds_the_sum_of_the_parts(self, radius):
        ops = TrimeshOps()
        a, b = box(20), sphere(radius)
        result = ops.union(a, b)
        assert result.ok
        volume = result.unwrap().volume
        assert volume <= a.volume + b.volume + 1e-6
        assert volume >= max(a.volume, b.volume) - 1e-6


class TestDecimation:
    def test_reduces_the_triangle_count(self, ops):
        dense = sphere(10)
        result = ops.decimate(dense, target_triangles=100)
        assert result.ok
        assert result.unwrap().triangle_count < dense.triangle_count

    def test_preserves_the_silhouette(self, ops):
        dense = sphere(10)
        reduced = ops.decimate(dense, target_triangles=200).unwrap()
        assert reduced.volume == pytest.approx(dense.volume, rel=0.05)

    def test_a_mesh_already_below_target_is_returned_unchanged(self, ops):
        simple = box()
        result = ops.decimate(simple, target_triangles=10_000)
        assert result.ok
        assert result.unwrap().triangle_count == simple.triangle_count

    def test_an_impossible_target_fails_clearly(self, ops):
        result = ops.decimate(sphere(), target_triangles=1)
        assert not result.ok
        assert "at least 4" in result.error


class TestWallThickness:
    def test_measures_a_thin_plate(self):
        plate = trimesh.creation.box(extents=(40, 40, 0.3))
        measured = thinnest_wall(
            Mesh(np.asarray(plate.vertices), np.asarray(plate.faces, np.int32))
        )
        assert measured is not None
        assert measured.millimetres == pytest.approx(0.3, abs=0.05)

    def test_measures_a_thick_block(self):
        measured = thinnest_wall(box(20))
        assert measured is not None
        assert measured.millimetres == pytest.approx(20.0, abs=1.0)

    def test_declines_to_guess_on_an_open_mesh(self):
        assert thinnest_wall(box_with_hole()) is None

    def test_declines_to_guess_on_an_empty_mesh(self):
        assert thinnest_wall(Mesh.empty()) is None
