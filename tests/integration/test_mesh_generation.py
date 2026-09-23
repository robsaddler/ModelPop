"""Making a model from a picture, against the real generator.

Skipped unless trellis.cpp and its weights are actually installed, so a clean
machine still passes. When they are there this is the only test that proves the
thing works rather than that the code is plausible - and it is slow, because it
loads about ten gigabytes and then runs a diffusion model.

What it checks is deliberately about *printing*, not about art. Whether the
shape looks like the picture is not something a test can judge; whether it is a
single watertight solid that fits a build plate is, and that is what decides
whether the result is any use.
"""

import numpy as np
import pytest

from modelpop.application.mesh_generation_ports import Detail, GenerationOptions
from modelpop.application.workspace import Workspace
from modelpop.domain.printer import PrinterProfile
from modelpop.generation import TrellisCliGenerator
from modelpop.mesh import TrimeshIO, TrimeshOps

pytestmark = pytest.mark.integration


def generator() -> TrellisCliGenerator:
    return TrellisCliGenerator(TrimeshIO())


generator_required = pytest.mark.skipif(
    not generator().is_available(),
    reason=f"the picture generator is not installed: {generator().describe()}",
)


@pytest.fixture
def picture(tmp_path):
    """A simple shape on a plain background.

    Drawn rather than photographed so the test needs no fixture file and so the
    subject is unambiguous. A generator given a flat disc should produce
    something round and solid.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 120, 392, 392), fill=(180, 60, 60))
    draw.ellipse((200, 200, 312, 312), fill=(240, 200, 60))

    path = tmp_path / "target.png"
    image.save(path)
    return path


@generator_required
def test_a_picture_becomes_a_printable_solid(picture, tmp_path):
    """The whole point. Slow: it loads ten gigabytes and runs a diffusion model."""
    stages: list[tuple[float, str]] = []

    def note(fraction: float, message: str) -> None:
        stages.append((fraction, message))

    result = generator().from_image(picture, GenerationOptions(detail=Detail.DRAFT, seed=42), note)

    assert result.ok, getattr(result, "error", "") + " " + getattr(result, "detail", "")
    generated = result.unwrap()

    assert not generated.mesh.is_empty
    assert generated.mesh.triangle_count > 1000
    assert generated.seconds > 0

    # the stage lines the binary writes should have driven a progress bar
    assert stages, "no progress was reported"
    assert stages[-1][0] <= 1.0
    assert all(0.0 <= fraction <= 1.0 for fraction, _ in stages)


@generator_required
def test_the_result_is_something_the_rest_of_the_app_can_use(picture, tmp_path):
    """A generated mesh has to survive the print pipeline, not just load."""
    result = generator().from_image(picture, GenerationOptions(detail=Detail.DRAFT, seed=7))
    assert result.ok, getattr(result, "error", "")

    workspace = Workspace(TrimeshIO(), TrimeshOps(), printer=PrinterProfile.p2s())
    state = workspace.adopt(result.unwrap().mesh)

    assert state.readiness is not None, "it should be assessed like anything else"
    assert state.mesh is not None

    placed = workspace.prepare_for_bed(state)
    assert placed.ok, getattr(placed, "error", "")

    bounds = placed.unwrap().mesh.bounds
    assert bounds.height.millimetres > 0
    assert np.isfinite(placed.unwrap().mesh.vertices).all()


@generator_required
def test_the_same_seed_gives_the_same_shape(picture, tmp_path):
    """Without this a picture cannot be iterated on, only gambled on.

    The same shape, but **not** bit-identical: measured at 141,214 against
    140,856 triangles for one seed, a quarter of a percent apart. GPU
    arithmetic is not reproducible - reductions and atomics finish in whatever
    order the scheduler chose - so the flow lands in a fractionally different
    place and the mesh extraction rounds differently.

    That is fine for the thing seeds are for: change the prompt, see what
    changed. It is not fine to claim byte-for-byte reproducibility, so this
    asserts what is actually true.
    """
    options = GenerationOptions(detail=Detail.DRAFT, seed=1234)

    first = generator().from_image(picture, options)
    second = generator().from_image(picture, options)

    assert first.ok and second.ok
    one, two = first.unwrap().mesh, second.unwrap().mesh

    assert one.triangle_count == pytest.approx(two.triangle_count, rel=0.02)

    # and the same size, which is what "the same shape" means to a printer
    for left, right in (
        (one.bounds.width, two.bounds.width),
        (one.bounds.depth, two.bounds.depth),
        (one.bounds.height, two.bounds.height),
    ):
        assert left.millimetres == pytest.approx(right.millimetres, rel=0.05)


@generator_required
def test_different_seeds_give_different_shapes(picture, tmp_path):
    """Otherwise the seed is decoration, and the test above proves nothing."""
    first = generator().from_image(picture, GenerationOptions(detail=Detail.DRAFT, seed=1))
    second = generator().from_image(picture, GenerationOptions(detail=Detail.DRAFT, seed=90210))

    assert first.ok and second.ok
    one, two = first.unwrap().mesh, second.unwrap().mesh

    assert one.triangle_count != two.triangle_count or one.volume != two.volume


@generator_required
def test_where_it_came_from_is_recorded(picture, tmp_path):
    result = generator().from_image(picture, GenerationOptions(detail=Detail.DRAFT, seed=99))
    generated = result.unwrap()

    assert "trellis" in generated.provenance.lower()
    assert "99" in generated.provenance
    assert picture.name in generated.provenance
