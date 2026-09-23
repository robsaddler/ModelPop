"""End-to-end slicing against the real Bambu Studio.

Marked ``integration``: excluded from the fast loop, and skipped entirely when
the slicer is not installed, so the suite still passes on a clean machine.

This is the test that proves the milestone in ``docs/00-plan.md``: a model goes
from geometry to printable G-code without leaving ModelPop.
"""

import numpy as np
import pytest
import trimesh

from modelpop.application import SliceJob
from modelpop.domain import Mesh
from modelpop.domain.printer import PrinterProfile, SupportType
from modelpop.mesh import TrimeshIO
from modelpop.printing import BambuSlicer

pytestmark = pytest.mark.integration

slicer_required = pytest.mark.skipif(
    not BambuSlicer().is_available(), reason="Bambu Studio is not installed"
)


@pytest.fixture
def overhanging_part() -> Mesh:
    """A 20 mm cube with an arm jutting out at mid-height.

    The arm is unsupported, so supports must be generated. A plain cube would
    slice happily without them and prove nothing.
    """
    cube = trimesh.creation.box(extents=(20, 20, 20))
    arm = trimesh.creation.box(extents=(15, 10, 5))
    arm.apply_translation((17.5, 0, 2.5))
    combined = trimesh.util.concatenate([cube, arm])
    return Mesh(
        np.asarray(combined.vertices, dtype=np.float64),
        np.asarray(combined.faces, dtype=np.int32),
    )


@slicer_required
def test_slices_a_model_into_printable_gcode(tmp_path, overhanging_part):
    model = tmp_path / "part.stl"
    assert TrimeshIO().save(overhanging_part, model).ok

    job = SliceJob(
        model_path=model,
        printer=PrinterProfile.p2s(),
        output_dir=tmp_path / "out",
        supports=SupportType.TREE_AUTO,
    )
    result = BambuSlicer().slice(job)

    assert result.ok, f"slicing failed: {getattr(result, 'error', '')}"
    report = result.unwrap()

    assert report.succeeded
    assert report.gcode_path is not None and report.gcode_path.stat().st_size > 10_000
    assert report.project_path is not None and report.project_path.exists()

    # the telemetry we feed into the readiness report
    assert report.predicted_seconds > 0
    assert report.layer_height is not None
    assert report.wall_loops >= 1
    assert report.supports_generated, "the overhang should have forced supports"
    assert report.objects, "the slicer should report what it placed"

    placed = report.objects[0]
    assert placed.triangle_count > 0
    assert placed.width.millimetres > 0


@slicer_required
def test_reports_a_missing_model_without_raising(tmp_path):
    job = SliceJob(
        model_path=tmp_path / "does-not-exist.stl",
        printer=PrinterProfile.p2s(),
        output_dir=tmp_path / "out",
    )
    result = BambuSlicer().slice(job)
    assert not result.ok
    assert "does not exist" in result.error


@slicer_required
def test_cleans_up_after_itself(tmp_path, overhanging_part):
    """A slice leaves hundreds of megabytes of scratch data; it must not persist."""
    import tempfile
    from pathlib import Path

    model = tmp_path / "part.stl"
    TrimeshIO().save(overhanging_part, model)
    before = set(Path(tempfile.gettempdir()).glob("modelpop-slice-*"))

    BambuSlicer().slice(
        SliceJob(model_path=model, printer=PrinterProfile.p2s(), output_dir=tmp_path / "out")
    )

    after = set(Path(tempfile.gettempdir()).glob("modelpop-slice-*"))
    assert after <= before, "the slicer left a working directory behind"


@slicer_required
def test_the_toolpath_of_a_real_slice_can_be_read_back(tmp_path, overhanging_part):
    """Proves the G-code dialect assumptions against the real slicer.

    The synthetic fixtures in tests/application/test_gcode.py encode three
    beliefs about Bambu's output: relative extrusion, CHANGE_LAYER markers, and
    Z_HEIGHT comments. If any were wrong the parser would be quietly useless, so
    one test reads a genuine file.
    """
    from modelpop.printing import verify_gcode

    model = tmp_path / "part.stl"
    TrimeshIO().save(overhanging_part, model)
    report = (
        BambuSlicer()
        .slice(
            SliceJob(
                model_path=model,
                printer=PrinterProfile.p2s(),
                output_dir=tmp_path / "out",
                supports=SupportType.TREE_AUTO,
            )
        )
        .unwrap()
    )

    assert report.gcode_path is not None
    verification = verify_gcode(report.gcode_path)

    assert verification.was_read, "the parser could not read real Bambu output"
    assert verification.layer_count > 10
    assert verification.tallest_z > 1.0
    assert verification.first_layer_area_mm2 > 0


@slicer_required
def test_a_floating_slab_is_caught_and_supports_fix_it(tmp_path):
    """A table with no supports floats its top; with supports it does not.

    This is the check that earns its place: it distinguishes a real printing
    failure from a sound print, using only the toolpath.
    """
    from modelpop.printing import verify_gcode

    legs = []
    for dx, dy in ((-17, -17), (17, -17), (-17, 17), (17, 17)):
        leg = trimesh.creation.box(extents=(6, 6, 25))
        leg.apply_translation((dx, dy, 0))
        legs.append(leg)
    top = trimesh.creation.box(extents=(46, 46, 4))
    top.apply_translation((0, 0, 14.5))
    table = trimesh.util.concatenate([*legs, top])

    mesh = (
        Mesh(
            np.asarray(table.vertices, dtype=np.float64),
            np.asarray(table.faces, dtype=np.int32),
        )
        .dropped_to_bed()
        .translated(128, 128, 0)
    )

    model = tmp_path / "table.stl"
    TrimeshIO().save(mesh, model)

    def slice_with(supports: SupportType, out: str):
        report = BambuSlicer().slice(
            SliceJob(
                model_path=model,
                printer=PrinterProfile.p2s(),
                output_dir=tmp_path / out,
                supports=supports,
                auto_orient=False,  # orienting it flat would remove the overhang
            )
        )
        assert report.ok, getattr(report, "error", "")
        return verify_gcode(report.unwrap().gcode_path)

    unsupported = slice_with(SupportType.NONE, "bare")
    supported = slice_with(SupportType.TREE_AUTO, "propped")

    assert unsupported.unsupported_layers >= 1, "the floating slab should be caught"
    assert supported.unsupported_layers == 0, "supports should hold the slab up"


@slicer_required
def test_a_real_slice_can_be_played_back(tmp_path, overhanging_part):
    """The virtual printer, against a genuine file.

    Proves the three things a synthetic fixture cannot: that the slicer's own
    M73 ladder is present and usable, that its stated total matches what the
    header says, and that the head climbs as the print runs.
    """
    from modelpop.printing import Clock, VirtualPrint

    model = tmp_path / "part.stl"
    TrimeshIO().save(overhanging_part, model)
    report = (
        BambuSlicer()
        .slice(
            SliceJob(
                model_path=model,
                printer=PrinterProfile.p2s(),
                output_dir=tmp_path / "out",
                supports=SupportType.TREE_AUTO,
            )
        )
        .unwrap()
    )

    play = VirtualPrint.read(report.gcode_path)

    assert play.can_play
    assert play.clock is Clock.SLICER, "the file should carry its own M73 ladder"
    assert play.total_seconds > 60

    # the slicer's telemetry and the file's own header must agree
    assert play.total_seconds == pytest.approx(report.predicted_seconds, rel=0.05)

    heights = [play.at_fraction(f).z for f in (0.0, 0.5, 1.0)]
    assert heights == sorted(heights)
    assert heights[-1] > heights[0]

    assert len(play.extruded_by(play.total_seconds)) > len(
        play.extruded_by(play.total_seconds * 0.1)
    )


@slicer_required
def test_the_filament_weight_is_computed_not_read(tmp_path, overhanging_part):
    """Bambu's P2S profile sets filament_density = 0.

    So the header's own weight line reads 0.00 for every print, and anything
    that trusts it reports a weightless model. This is the AMS comparison's
    input, so it has to be right.
    """
    from modelpop.printing import Toolpath

    model = tmp_path / "part.stl"
    TrimeshIO().save(overhanging_part, model)
    report = (
        BambuSlicer()
        .slice(
            SliceJob(model_path=model, printer=PrinterProfile.p2s(), output_dir=tmp_path / "out")
        )
        .unwrap()
    )

    toolpath = Toolpath.read(report.gcode_path)

    assert toolpath.filament_mm > 0, "the header states a length even when weight is zero"
    assert toolpath.grams() > 0.5, "a 20 mm cube with an arm weighs more than half a gram"
    assert toolpath.flush.slots > 0, "the flush matrix should be present for the AMS comparison"


@slicer_required
def test_a_real_slice_draws_as_a_toolpath(tmp_path, overhanging_part):
    """The print preview, against a genuine file.

    Builds the geometry the viewport would draw, without rasterising it, and
    checks the three things that would make the view wrong rather than merely
    ugly: the right number of lines, real feature colours, and the whole thing
    sitting on the build plate in millimetres.
    """
    from modelpop.printing import VirtualPrint
    from modelpop.rendering.print_view import DEFAULT_COLOUR, to_lines

    model = tmp_path / "part.stl"
    TrimeshIO().save(overhanging_part, model)
    report = (
        BambuSlicer()
        .slice(
            SliceJob(
                model_path=model,
                printer=PrinterProfile.p2s(),
                output_dir=tmp_path / "out",
                supports=SupportType.TREE_AUTO,
            )
        )
        .unwrap()
    )

    play = VirtualPrint.read(report.gcode_path)
    laid = play.extruded_by(play.total_seconds)
    poly = to_lines(laid)

    assert poly.n_cells == len(laid)
    assert poly.n_cells > 500

    # real output carries several named features, not one undifferentiated blob
    features = {s.feature for s in laid if s.feature}
    assert len(features) >= 3, f"only saw {features}"

    colours = {tuple(row) for row in poly.cell_data["feature"]}
    assert len(colours) >= 2, "a real print should not be drawn in one colour"

    default = tuple(int(DEFAULT_COLOUR[i : i + 2], 16) for i in (1, 3, 5))
    unrecognised = sum(1 for row in poly.cell_data["feature"] if tuple(row) == default)
    assert unrecognised < poly.n_cells * 0.6, (
        f"most moves fell back to the default colour; the feature table is stale. Saw {features}"
    )

    # drawn on the plate, in millimetres
    assert poly.bounds[0] >= 0 and poly.bounds[1] <= 256
    assert poly.bounds[4] >= 0
