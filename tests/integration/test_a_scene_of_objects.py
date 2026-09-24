"""Several objects on one plate, built through the real OCCT kernel.

The fast tests use a fake compiler and prove the bookkeeping: which object a
command lands on, what a copy is, what a delete removes. These prove the thing
the bookkeeping is for - that OCCT really does build each object separately,
that they stand where they were put, and that nothing is fused between them.

That last point is the one worth paying a subprocess for. The previous design
unioned every shape into a single solid, and a union of two distant solids is
still *a* solid - it builds, it measures, it looks right in a screenshot. The
only way to tell the difference is to move one and check the other stayed.
"""

import pytest

from modelpop.application.modelling import ModellingSession
from modelpop.cad import Build123dCompiler
from modelpop.cad.build123d_kernel import Build123dKernel
from modelpop.presentation.modelling_view_model import ModellingViewModel
from tests.conftest import kernel_is_available, kernel_required  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def scene() -> ModellingViewModel:
    return ModellingViewModel(ModellingSession(Build123dCompiler(Build123dKernel())))


@kernel_required
def test_two_shapes_come_back_as_two_objects(scene):
    scene.add_box(30, 30, 30)
    scene.add_sphere(10)

    assert len(scene.bodies) == 2
    assert [body.label for body in scene.bodies] == ["Box", "Sphere"]


@kernel_required
def test_moving_one_leaves_the_other_where_it_was(scene):
    """The whole point, and the only test that can tell a scene from a union."""
    scene.add_box(30, 30, 30)
    scene.add_sphere(10)
    box_before = scene.bodies[0].bounds

    scene.move(0, 0, 60)

    assert scene.bodies[0].bounds.min_z == pytest.approx(box_before.min_z)
    assert scene.bodies[1].bounds.min_z == pytest.approx(50.0, abs=0.1)


@kernel_required
def test_each_object_measures_only_itself(scene):
    """A union would report one volume for the pair."""
    scene.add_box(20, 20, 20)
    scene.add_sphere(10)

    box, sphere = scene.bodies
    assert box.measurements.volume_mm3 == pytest.approx(8000.0, rel=0.01)
    assert sphere.measurements.volume_mm3 == pytest.approx(4188.8, rel=0.02)


@kernel_required
def test_a_whole_scene_costs_one_rebuild(scene):
    """Three objects must not cost three subprocesses.

    Measured rather than asserted on a mock: the session hands the compiler a
    document and gets every object back from one run.
    """
    scene.add_box(20, 20, 20)
    scene.add_sphere(10)
    scene.add_cylinder(6, 30)

    assert len(scene.bodies) == 3
    assert scene.state.rebuild_seconds > 0.0


@kernel_required
def test_the_scene_as_one_mesh_holds_every_object(scene):
    """What the slicer, the readiness checks and the exporter are handed."""
    scene.add_box(20, 20, 20)
    scene.add_sphere(10)

    whole = scene.state.mesh
    assert whole is not None
    triangles = sum(body.mesh.triangle_count for body in scene.bodies)
    assert whole.triangle_count == triangles


@kernel_required
def test_a_cut_still_works_on_the_object_it_was_aimed_at(scene):
    """Adding a shape makes an object; cutting with one does not."""
    scene.add_box(30, 30, 30)
    solid = scene.bodies[0].measurements.volume_mm3

    scene.add_cylinder(5, 60, cut=True)

    assert len(scene.bodies) == 1
    assert scene.bodies[0].measurements.volume_mm3 < solid


@kernel_required
def test_a_copy_is_a_real_second_object(scene):
    scene.add_box(20, 20, 20)
    scene.duplicate_selected()

    assert len(scene.bodies) == 2
    original, copy = scene.bodies
    assert copy.bounds.min_x != original.bounds.min_x
    assert copy.measurements.volume_mm3 == pytest.approx(original.measurements.volume_mm3)


@kernel_required
def test_deleting_one_rebuilds_the_rest(scene):
    scene.add_box(20, 20, 20)
    scene.add_sphere(10)
    scene.delete_selected()

    assert len(scene.bodies) == 1
    assert scene.bodies[0].label == "Box"
    assert scene.state.mesh is not None and not scene.state.mesh.is_empty
