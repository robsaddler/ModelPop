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
