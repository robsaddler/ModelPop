"""A real reconstruction, through the real COLMAP and the real OpenMVS.

Skipped when either is missing, which is most machines. Nothing about this can
be faked usefully: the two failure modes that cost real time - a mapper that
solves nothing while exiting zero, and a mesher that says nothing on stdout -
only happen against the actual programs.

The photographs are *rendered*, deliberately. A committed folder of real photos
would be tens of megabytes, and a render is reproducible: the same seed gives
the same views every time, so a failure here is a change in the code or the
tools rather than in the weather.
"""

from __future__ import annotations

import pytest

from modelpop.application.reconstruction_ports import Quality, ReconstructionOptions
from modelpop.domain.photo_set import PhotoSet
from modelpop.domain.units import Length
from modelpop.mesh import TrimeshIO
from modelpop.vision.photogrammetry import (
    ColmapOpenMvsReconstructor,
    find_colmap,
    find_openmvs,
)

pytestmark = pytest.mark.integration

tools_required = pytest.mark.skipif(
    not (find_colmap() and find_openmvs()),
    reason="COLMAP and OpenMVS are not installed - see docs/12-photogrammetry.md",
)


@pytest.fixture(scope="module")
def photographs(tmp_path_factory) -> PhotoSet:
    """Views of a lumpy, densely textured object on a textured ground.

    Texture is the point. A smooth grey object reconstructs into nothing, and
    that is a property of photogrammetry rather than a bug - so a test set that
    was easy to match would prove nothing about the adapter.
    """
    # Imported here rather than at the top of the file. The *fast* suite still
    # collects this module before deselecting it, and paying two seconds to
    # import a renderer for tests that will not run is the same mistake the
    # kernel checks made - see tests/conftest.py.
    import numpy as np
    import pyvista as pv

    out = tmp_path_factory.mktemp("photos")
    rng = np.random.default_rng(11)

    subject = pv.Sphere(radius=1.0, theta_resolution=120, phi_resolution=120).triangulate()
    points = subject.points.copy()
    wobble = (
        1.0
        + 0.10 * np.sin(5 * points[:, 0]) * np.cos(4 * points[:, 1])
        + 0.05 * np.sin(9 * points[:, 2])
    )
    subject.points = points * wobble[:, None]
    subject["tex"] = rng.random(subject.n_points)

    ground = pv.Plane(
        center=(0, 0, -1.2),
        direction=(0, 0, 1),
        i_size=6,
        j_size=6,
        i_resolution=120,
        j_resolution=120,
    ).triangulate()
    ground["tex"] = rng.random(ground.n_points)

    plotter = pv.Plotter(off_screen=True, window_size=(1024, 768))
    plotter.add_mesh(subject, scalars="tex", cmap="turbo", show_scalar_bar=False)
    plotter.add_mesh(ground, scalars="tex", cmap="gist_earth", show_scalar_bar=False)
    plotter.set_background("white")
    plotter.enable_lightkit()

    count = 24
    for index in range(count):
        angle = 360.0 * index / count
        elevation = 25.0 + 12.0 * np.sin(np.radians(angle * 2))
        radius = 4.2
        plotter.camera_position = [
            (
                radius * np.cos(np.radians(angle)),
                radius * np.sin(np.radians(angle)),
                radius * np.sin(np.radians(elevation)),
            ),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
        ]
        plotter.render()
        plotter.screenshot(str(out / f"photo_{index:03d}.png"))
    plotter.close()

    return PhotoSet.of(out.glob("*.png"))


@pytest.fixture
def reconstructor() -> ColmapOpenMvsReconstructor:
    # No lease: the fixture must not contend with a generation the developer is
    # running in the app, and nothing else in this test wants the card.
    return ColmapOpenMvsReconstructor(mesh_io=TrimeshIO(), lease=None)


@tools_required
@pytest.mark.renders
def test_photographs_become_a_measured_model(reconstructor, photographs):
    """The whole point: 24 views in, one surface out, measured not invented."""
    outcome = reconstructor.reconstruct(
        photographs,
        ReconstructionOptions(quality=Quality.DRAFT, size=Length.mm(120)),
    )

    assert outcome.ok, getattr(outcome, "error", "")
    built = outcome.unwrap()

    assert built.mesh.triangle_count > 10_000, "a surface, not a handful of points"
    assert built.photos_used >= 20, f"only placed {built.photos_used} of 24"
    assert not built.is_thin


@tools_required
@pytest.mark.renders
def test_the_model_comes_out_the_size_that_was_asked_for(reconstructor, photographs):
    """A reconstruction has no scale of its own, so one has to be given."""
    built = reconstructor.reconstruct(
        photographs,
        ReconstructionOptions(quality=Quality.DRAFT, size=Length.mm(90)),
    ).unwrap()

    assert built.mesh.bounds.largest_dimension.millimetres == pytest.approx(90.0, abs=0.5)


@tools_required
@pytest.mark.renders
def test_it_says_the_size_was_chosen_rather_than_measured(reconstructor, photographs):
    """A chosen size cannot be checked with calipers and a measured one can.

    On screen the two are identical, so the note is the only thing that keeps
    them apart.
    """
    built = reconstructor.reconstruct(
        photographs, ReconstructionOptions(quality=Quality.DRAFT)
    ).unwrap()

    assert any("carry no scale" in note for note in built.notes)


@tools_required
@pytest.mark.renders
def test_progress_runs_forward_through_every_stage(reconstructor, photographs):
    seen: list[tuple[float, str]] = []
    reconstructor.reconstruct(
        photographs,
        ReconstructionOptions(quality=Quality.DRAFT),
        lambda fraction, phrase: seen.append((fraction, phrase)),
    )

    assert len(seen) == 7, "one report per stage"
    assert [f for f, _ in seen] == sorted(f for f, _ in seen)
    assert all(0.0 <= f <= 1.0 for f, _ in seen)


@tools_required
def test_photographs_that_cannot_be_pieced_together_say_why(reconstructor, tmp_path):
    """The real failure of photogrammetry, and it must not be a traceback.

    Four images of nothing in particular: the solver will place none of them,
    write no model, and exit zero. The adapter has to notice that silence.
    """
    from PIL import Image

    for index in range(4):
        Image.new("RGB", (320, 240), (40 + index, 40, 40)).save(tmp_path / f"flat_{index}.png")

    outcome = reconstructor.reconstruct(
        PhotoSet.of(tmp_path.glob("*.png")),
        ReconstructionOptions(quality=Quality.DRAFT),
    )

    assert not outcome.ok
    assert "could not be pieced together" in outcome.reason
    assert "overlap" in outcome.detail
