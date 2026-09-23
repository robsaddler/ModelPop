import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from modelpop.domain import Length, Mesh, Unit

from .strategies import (
    boxes,
    degenerate_sliver,
    duplicate_vertices,
    meshes,
    single_triangle,
    tetrahedron,
    unit_cube,
)


class TestValidation:
    """A Mesh that exists is a Mesh that is structurally sound."""

    def test_rejects_wrong_vertex_shape(self):
        with pytest.raises(ValueError, match="vertices must have shape"):
            Mesh(np.zeros((4, 2)), np.zeros((0, 3), np.int32))

    def test_rejects_wrong_face_shape(self):
        with pytest.raises(ValueError, match="faces must have shape"):
            Mesh(np.zeros((4, 3)), np.zeros((2, 4), np.int32))

    def test_rejects_face_index_out_of_range(self):
        with pytest.raises(ValueError, match="face indices"):
            Mesh(np.zeros((3, 3)), np.array([[0, 1, 99]], np.int32))

    def test_rejects_negative_face_index(self):
        with pytest.raises(ValueError, match="face indices"):
            Mesh(np.zeros((3, 3)), np.array([[0, -1, 2]], np.int32))

    @pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
    def test_rejects_non_finite_vertices(self, bad):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [0, bad, 0]], dtype=np.float64)
        with pytest.raises(ValueError, match="NaN or infinity"):
            Mesh(vertices, np.array([[0, 1, 2]], np.int32))

    def test_arrays_are_read_only_so_a_mesh_is_really_a_value(self):
        mesh = unit_cube()
        with pytest.raises(ValueError):
            mesh.vertices[0, 0] = 99.0

    def test_accepts_degenerate_geometry_that_is_structurally_valid(self):
        # A sliver is bad geometry but a legal mesh. Rejecting it here would stop
        # us loading the very files we exist to repair.
        assert degenerate_sliver().triangle_count == 1
        assert duplicate_vertices().vertex_count == 4


class TestMeasurement:
    def test_unit_cube_has_unit_volume(self):
        assert unit_cube().volume == pytest.approx(1.0)

    def test_cube_volume_is_the_cube_of_its_size(self):
        assert unit_cube(5.0).volume == pytest.approx(125.0)

    def test_unit_cube_has_six_unit_faces(self):
        assert unit_cube().surface_area == pytest.approx(6.0)

    def test_tetrahedron_volume_matches_the_formula(self):
        # a corner tetrahedron of side s has volume s^3 / 6
        assert tetrahedron(3.0).volume == pytest.approx(27.0 / 6.0)

    def test_open_mesh_has_no_meaningful_volume_but_has_area(self):
        triangle = single_triangle()
        assert triangle.surface_area == pytest.approx(0.5)
        assert triangle.volume == pytest.approx(0.0, abs=1e-12)

    def test_empty_mesh_measures_zero_rather_than_exploding(self):
        empty = Mesh.empty()
        assert empty.is_empty
        assert empty.volume == 0.0
        assert empty.surface_area == 0.0
        assert empty.bounds.width == Length.mm(0)

    def test_bounds_are_reported_in_millimetres_whatever_the_mesh_unit(self):
        in_inches = unit_cube(1.0).with_unit(Unit.INCH)
        assert in_inches.bounds.width.millimetres == pytest.approx(25.4)

    def test_volume_scales_with_the_unit(self):
        # one cubic inch is 25.4^3 cubic millimetres
        assert unit_cube(1.0).with_unit(Unit.INCH).volume == pytest.approx(25.4**3)

    def test_volume_survives_a_small_mesh_far_from_the_origin(self):
        """Regression: catastrophic cancellation in the divergence-theorem volume.

        Found by hypothesis on the first run. A 0.1 mm box at x=36 was measured
        wrong by over 100% because the cross-product terms (~1300) dwarfed the
        answer (~0.001). The fix shifts vertices to the bounding-box centre
        first; volume is translation-invariant so the maths is unchanged.
        """
        tiny_and_distant = unit_cube(0.1, origin=(36.0, 36.0, 36.0))
        assert tiny_and_distant.volume == pytest.approx(0.001, rel=1e-9)

    @pytest.mark.parametrize("distance", [0, 1e2, 1e4, 1e5])
    def test_volume_is_independent_of_distance_from_the_origin(self, distance):
        at_origin = unit_cube(0.5).volume
        far_away = unit_cube(0.5, origin=(distance, distance, distance)).volume
        assert far_away == pytest.approx(at_origin, rel=1e-9)


class TestTransforms:
    """The invariants that matter: what must and must not change."""

    @given(boxes(), st.floats(min_value=0.01, max_value=100))
    def test_scaling_by_s_then_one_over_s_returns_the_original(self, mesh, factor):
        there_and_back = mesh.scaled(factor).scaled(1 / factor)
        assert there_and_back.volume == pytest.approx(mesh.volume, rel=1e-6)

    @given(boxes(), st.floats(min_value=0.1, max_value=10))
    def test_scaling_cubes_the_volume(self, mesh, factor):
        assert mesh.scaled(factor).volume == pytest.approx(mesh.volume * factor**3, rel=1e-9)

    @given(boxes(), st.floats(min_value=-100, max_value=100))
    def test_translation_changes_nothing_but_position(self, mesh, offset):
        moved = mesh.translated(offset, offset, offset)
        assert moved.volume == pytest.approx(mesh.volume, rel=1e-9)
        assert moved.surface_area == pytest.approx(mesh.surface_area, rel=1e-9)
        assert moved.triangle_count == mesh.triangle_count

    @given(boxes())
    def test_centring_puts_the_bounding_box_centre_on_the_origin(self, mesh):
        centre = mesh.centred_on_origin().bounds.centre
        assert centre == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)

    @given(boxes())
    def test_dropping_to_the_bed_puts_the_lowest_point_on_z_zero(self, mesh):
        dropped = mesh.dropped_to_bed()
        assert dropped.bounds.min_z == pytest.approx(0.0, abs=1e-9)
        cx, cy, _ = dropped.bounds.centre
        assert (cx, cy) == pytest.approx((0.0, 0.0), abs=1e-9)

    @given(boxes(), st.floats(min_value=1, max_value=250))
    def test_scaled_to_fit_hits_the_requested_size(self, mesh, target_mm):
        resized = mesh.scaled_to_fit(Length.mm(target_mm))
        assert resized.bounds.largest_dimension.millimetres == pytest.approx(target_mm, rel=1e-6)

    def test_scaled_to_fit_is_how_six_inches_tall_is_honoured(self):
        # the brief: "an MSI dragon, about 6 inches tall"
        dragon = unit_cube(3.0)  # stand-in
        sized = dragon.scaled_to_fit(Length.inches(6))
        assert sized.bounds.largest_dimension.millimetres == pytest.approx(152.4)

    def test_scaled_to_fit_leaves_an_empty_mesh_alone(self):
        assert Mesh.empty().scaled_to_fit(Length.mm(50)).is_empty


class TestUnits:
    @given(meshes())
    def test_converting_units_preserves_physical_size(self, mesh):
        for unit in Unit:
            converted = mesh.to_unit(unit)
            assert converted.unit is unit
            assert converted.bounds.width.millimetres == pytest.approx(
                mesh.bounds.width.millimetres, rel=1e-9
            )

    @given(meshes())
    def test_unit_round_trip_is_lossless(self, mesh):
        there_and_back = mesh.to_unit(Unit.INCH).to_unit(mesh.unit)
        assert there_and_back.bounds.width.millimetres == pytest.approx(
            mesh.bounds.width.millimetres, rel=1e-9
        )

    def test_reinterpreting_units_changes_physical_size_deliberately(self):
        # with_unit is for files that lied about their units
        cube = unit_cube(1.0)
        assert cube.bounds.width == Length.mm(1)
        assert cube.with_unit(Unit.INCH).bounds.width == Length.inches(1)


class TestIdentity:
    @given(boxes())
    def test_equal_content_means_equal_meshes(self, mesh):
        twin = Mesh(mesh.vertices.copy(), mesh.faces.copy(), mesh.unit)
        assert twin == mesh
        assert hash(twin) == hash(mesh)

    def test_different_geometry_means_different_meshes(self):
        assert unit_cube(1.0) != unit_cube(2.0)

    def test_the_same_shape_in_different_units_is_not_the_same_mesh(self):
        assert unit_cube(1.0) != unit_cube(1.0).with_unit(Unit.INCH)

    @given(boxes())
    def test_content_hash_is_stable_across_calls(self, mesh):
        assert mesh.content_hash == mesh.content_hash

    def test_content_hash_ignores_insignificant_float_drift(self):
        cube = unit_cube()
        nudged = Mesh(cube.vertices + 1e-12, cube.faces, cube.unit)
        assert nudged.content_hash == cube.content_hash

    def test_meshes_can_key_a_cache(self):
        assert len({unit_cube(), unit_cube(), unit_cube(2.0)}) == 2


class TestBoundingBox:
    def test_reports_the_printer_envelope_correctly(self):
        p2s = (Length.mm(256), Length.mm(256), Length.mm(256))
        assert unit_cube(100).bounds.fits_within(*p2s)
        assert not unit_cube(300).bounds.fits_within(*p2s)

    def test_a_tall_thin_model_fits_but_a_wide_one_may_not(self):
        tall = Mesh(
            np.array(
                [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 250]], dtype=np.float64
            ),
            np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int32),
        )
        envelope = (Length.mm(256), Length.mm(256), Length.mm(256))
        assert tall.bounds.fits_within(*envelope)
        assert tall.bounds.height == Length.mm(250)
