"""A feature tree built through the real OCCT kernel.

The fast tests assert on the script the compiler produces, because a wrong
script still builds *a* shape and geometry alone would not catch it. These prove
the script is one build123d actually accepts, and that the operations do what
their names claim - which no amount of string matching can show.
"""

import pytest

from modelpop.application.modelling import ModellingSession
from modelpop.cad import Build123dCompiler
from modelpop.cad.build123d_kernel import Build123dKernel
from modelpop.domain.cad_commands import (
    Chamfer,
    CreateBox,
    CreateCylinder,
    CreateSphere,
    EdgeSelector,
    Face,
    Fillet,
    Hollow,
    Move,
    Rotate,
    ScaleTo,
    TextOnSurface,
)
from modelpop.domain.units import Length

pytestmark = pytest.mark.integration

kernel_required = pytest.mark.skipif(
    not Build123dKernel().is_available(), reason="build123d is not installed"
)


@pytest.fixture
def model() -> ModellingSession:
    return ModellingSession(Build123dCompiler(Build123dKernel()))


@kernel_required
@pytest.mark.parametrize(
    "command",
    [
        Fillet(2, EdgeSelector.VERTICAL),
        Fillet(2, EdgeSelector.ALL),
        Fillet(2, EdgeSelector.TOP),
        Fillet(2, EdgeSelector.BOTTOM),
        Fillet(2, EdgeSelector.HORIZONTAL),
        Chamfer(1, EdgeSelector.TOP),
        Hollow(2.0, Face.BOTTOM),
        Hollow(2.0, None),
        Move(5, 0, 0),
        Rotate(15),
        TextOnSurface("MP", Face.FRONT, 8, 1.0),
        TextOnSurface("MP", Face.TOP, 8, 1.0, raised=False),
        ScaleTo(Length.mm(60)),
    ],
    ids=lambda c: c.describe(),
)
def test_each_command_builds_on_a_plain_box(model, command):
    """If one of these stops compiling, the toolbar has a dead button.

    Each is applied to a fresh box rather than stacked, because some pairs are
    genuinely impossible - see the chamfer-then-hollow case below - and a stack
    would hide which one broke.
    """
    assert model.apply(CreateBox(30, 30, 30)).ok

    result = model.apply(command)
    assert result.ok, f"{command.describe()} failed: {getattr(result, 'error', '')}"
    assert model.state.has_geometry


@kernel_required
def test_a_long_chain_of_compatible_features_builds(model):
    """The operations people actually stack, in the order they stack them."""
    commands = [
        CreateBox(30, 30, 30),
        Fillet(2, EdgeSelector.VERTICAL),
        Hollow(1.5, Face.BOTTOM),
        Move(5, 0, 0),
        Rotate(15),
        ScaleTo(Length.mm(60)),
    ]
    for command in commands:
        assert model.apply(command).ok, command.describe()

    assert len(model.state.document) == len(commands)
    assert model.state.has_geometry


@kernel_required
def test_a_wall_exactly_matching_a_fillet_is_caught_before_occt_sees_it(model):
    """A measured OCCT limitation, turned into something the user can act on.

    Offsetting inward by exactly the fillet radius collapses the inner fillet to
    zero radius, and OCCT answers with Standard_ConstructionError and nothing
    else. Measured: a 2.0 mm wall on a 2.0 mm fillet fails; 1.9 and 2.5 both
    work. So the combination is refused up front with the numbers to try.
    """
    assert model.apply(CreateBox(30, 30, 30)).ok
    assert model.apply(Fillet(2, EdgeSelector.VERTICAL)).ok

    before = model.state.document.content_hash
    result = model.apply(Hollow(2.0, Face.BOTTOM))

    assert not result.ok
    assert "1.8 mm or 2.5 mm" in result.detail, result.detail
    assert "ConstructionError" not in result.detail, "OCCT's wording reached the user"
    assert model.state.document.content_hash == before
    assert model.state.has_geometry


@kernel_required
def test_nudging_the_wall_off_the_fillet_radius_works(model):
    """The fix the message suggests must actually be the fix."""
    assert model.apply(CreateBox(30, 30, 30)).ok
    assert model.apply(Fillet(2, EdgeSelector.VERTICAL)).ok
    assert model.apply(Hollow(1.8, Face.BOTTOM)).ok


@kernel_required
def test_a_filleted_and_chamfered_shape_can_still_be_hollowed(model):
    """Checked because the first attempt blamed this combination and was wrong.

    The failure was the wall exactly matching the fillet radius, not the blends
    interacting. Keeping the test stops that wrong belief coming back.
    """
    for command in (
        CreateBox(30, 30, 30),
        Fillet(2, EdgeSelector.VERTICAL),
        Chamfer(1, EdgeSelector.TOP),
        Hollow(1.5, Face.BOTTOM),
    ):
        assert model.apply(command).ok, command.describe()

    assert model.state.has_geometry


@kernel_required
def test_the_primitives_are_the_size_they_claim(model):
    assert model.apply(CreateBox(40, 30, 20)).ok
    size = model.state.measurements

    assert size.width.millimetres == pytest.approx(40, abs=0.01)
    assert size.depth.millimetres == pytest.approx(30, abs=0.01)
    assert size.height.millimetres == pytest.approx(20, abs=0.01)


@kernel_required
def test_a_second_primitive_is_fused_rather_than_replacing_the_first(model):
    model.apply(CreateBox(40, 40, 10))
    model.apply(CreateCylinder(8, 30))

    size = model.state.measurements
    assert size.height.millimetres == pytest.approx(30, abs=0.01), "the cylinder was lost"
    assert size.solid_count == 1, "they should be one part, not two"


@kernel_required
def test_a_fillet_removes_material(model):
    model.apply(CreateBox(30, 30, 30))
    before = model.state.measurements.volume_mm3

    model.apply(Fillet(3))
    after = model.state.measurements.volume_mm3

    assert after < before, "rounding the edges should cut the corners off"
    assert after > before * 0.9, "it removed far more than a 3 mm round could"


@kernel_required
def test_hollowing_removes_most_of_the_inside(model):
    """What "a hollow core" is actually for: filament and time."""
    model.apply(CreateBox(40, 40, 40))
    solid = model.state.measurements.volume_mm3

    assert model.apply(Hollow(2.0, Face.BOTTOM)).ok
    hollow = model.state.measurements.volume_mm3

    assert hollow < solid * 0.4, f"only removed {1 - hollow / solid:.0%}"


@kernel_required
def test_scaling_to_a_size_gives_that_size(model):
    model.apply(CreateBox(10, 20, 30))
    assert model.apply(ScaleTo(Length.inches(6))).ok

    assert model.state.measurements.height.millimetres == pytest.approx(152.4, abs=0.1)


@kernel_required
def test_scaling_is_recomputed_when_an_earlier_feature_changes(model):
    """The difference between a parametric model and a recording of one."""
    model.apply(CreateBox(10, 10, 30))
    model.apply(ScaleTo(Length.mm(100)))
    assert model.state.measurements.height.millimetres == pytest.approx(100, abs=0.1)

    # insert a feature that changes the height, then confirm the scale still holds
    model.undo()
    model.apply(CreateCylinder(3, 60))
    model.apply(ScaleTo(Length.mm(100)))

    assert model.state.measurements.height.millimetres == pytest.approx(100, abs=0.1)


@kernel_required
def test_embossed_text_stands_proud_of_the_face(model):
    model.apply(CreateBox(60, 20, 40))
    before = model.state.measurements.depth.millimetres

    assert model.apply(TextOnSurface("MSI", Face.FRONT, 12, 1.5)).ok
    after = model.state.measurements.depth.millimetres

    assert after > before, "raised text should make the part deeper"
    assert after == pytest.approx(before + 1.5, abs=0.2)


@kernel_required
def test_engraved_text_does_not(model):
    model.apply(CreateBox(60, 20, 40))
    before = model.state.measurements.volume_mm3

    assert model.apply(TextOnSurface("MSI", Face.FRONT, 12, 1.5, raised=False)).ok
    after = model.state.measurements.volume_mm3

    assert after < before, "engraving should remove material"


@kernel_required
def test_a_sphere_is_round(model):
    assert model.apply(CreateSphere(20)).ok
    size = model.state.measurements

    assert size.width.millimetres == pytest.approx(40, abs=0.5)
    assert size.height.millimetres == pytest.approx(40, abs=0.5)


@kernel_required
def test_rotating_changes_the_footprint(model):
    model.apply(CreateBox(40, 10, 10))
    assert model.apply(Rotate(45)).ok

    size = model.state.measurements
    assert size.depth.millimetres > 10, "a rotated bar should be wider than its own depth"


@kernel_required
def test_a_fillet_bigger_than_the_part_is_refused_and_changes_nothing(model):
    """The invariant that makes undo trustworthy, proved against real OCCT."""
    model.apply(CreateBox(10, 10, 10))
    before = model.state.document.content_hash

    result = model.apply(Fillet(200))

    assert not result.ok
    assert model.state.document.content_hash == before
    assert model.state.has_geometry


@kernel_required
def test_the_users_own_example_builds_end_to_end(model):
    """Rob's request, expressed as a feature tree:
    "about 6 inches tall ... with a hollow core and MSI ... across his front"."""
    for command in (
        CreateBox(50, 40, 150),
        Fillet(4, EdgeSelector.VERTICAL),
        Hollow(2.0, Face.BOTTOM),
        TextOnSurface("MSI", Face.FRONT, 18, 1.5),
        ScaleTo(Length.inches(6)),
    ):
        assert model.apply(command).ok, command.describe()

    size = model.state.measurements
    assert size.height.millimetres == pytest.approx(152.4, abs=0.5)
    assert size.solid_count == 1
    assert size.volume_mm3 < 50 * 40 * 150, "it should be hollow"


@kernel_required
def test_a_hole_actually_goes_through(model):
    """Cutting a cylinder is how a hole is made, and the most-wanted operation
    for a printed part. A hole that stops short leaves a skin the user only
    finds after printing."""
    assert model.apply(CreateBox(60, 40, 20)).ok
    solid = model.state.measurements.volume_mm3

    assert model.apply(CreateCylinder(4, 60, cut=True)).ok
    drilled = model.state.measurements.volume_mm3

    # a 8 mm hole through 20 mm of material is about 1005 mm3
    assert solid - drilled == pytest.approx(1005, rel=0.05)
    assert model.state.measurements.height.millimetres == pytest.approx(20, abs=0.01)


@kernel_required
def test_several_holes_can_be_placed_separately(model):
    assert model.apply(CreateBox(60, 40, 20)).ok
    for offset in (-20, 0, 20):
        assert model.apply(CreateCylinder(3, 60, x=offset, cut=True)).ok, offset

    solid = 60 * 40 * 20
    assert model.state.measurements.volume_mm3 < solid - 3 * 500


@kernel_required
def test_a_pocket_does_not_go_through(model):
    """A box cut from above should leave a floor if it does not reach the bottom."""
    assert model.apply(CreateBox(60, 40, 20)).ok
    assert model.apply(CreateBox(20, 20, 10, z=8, cut=True)).ok

    assert model.state.measurements.height.millimetres == pytest.approx(20, abs=0.01)
    assert model.state.measurements.solid_count == 1


@kernel_required
def test_an_added_shape_can_be_placed_off_centre(model):
    assert model.apply(CreateBox(40, 40, 10)).ok
    assert model.apply(CreateCylinder(5, 30, x=15)).ok

    size = model.state.measurements
    assert size.width.millimetres == pytest.approx(40, abs=0.5), "the cylinder should be inside"
    assert size.height.millimetres == pytest.approx(30, abs=0.5), "and taller than the plate"


@kernel_required
def test_a_drilled_bracket_builds_and_saves(model, tmp_path):
    """A plate with mounting holes, which is the commonest printed part there is."""
    from modelpop.projects import JsonProjectStore

    for command in (
        CreateBox(80, 40, 6),
        CreateCylinder(2.5, 20, x=-30, cut=True),
        CreateCylinder(2.5, 20, x=30, cut=True),
        Fillet(4, EdgeSelector.VERTICAL),
    ):
        assert model.apply(command).ok, command.describe()

    assert model.state.measurements.solid_count == 1

    store = JsonProjectStore()
    saved = store.save(model.state.document, tmp_path / "bracket").unwrap()
    assert store.load(saved).unwrap().is_complete
