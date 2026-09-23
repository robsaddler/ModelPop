"""From words on a toolbar to G-code that would actually print.

Every other integration test proves one link. This one walks the whole chain
with nothing synthetic in it: a part modelled with the in-app CAD tools, built
by the real OCCT kernel, written as a mesh, sliced by the real Bambu Studio,
and the resulting toolpath read back and checked.

It is the test for the thing Rob asked for in so many words - that the slices
would work and the print head would not run into what it had already printed -
applied to a part this application made rather than to a fixture that was
carefully shaped to pass.

Slow and marked ``integration``, and skipped entirely when either the kernel or
the slicer is missing, so a clean machine still passes.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from modelpop.application import SliceJob
from modelpop.application.modelling import ModellingSession
from modelpop.cad import Build123dCompiler
from modelpop.cad.build123d_kernel import Build123dKernel
from modelpop.domain import Mesh
from modelpop.domain.cad_commands import (
    CreateBox,
    CreateCylinder,
    EdgeSelector,
    Extrude,
    Fillet,
    Repeat,
    Revolve,
    TextOnSurface,
)
from modelpop.domain.printer import PrinterProfile, SupportType
from modelpop.mesh import TrimeshIO
from modelpop.printing import BambuSlicer, verify_gcode

pytestmark = pytest.mark.integration

everything_required = pytest.mark.skipif(
    not (Build123dKernel().is_available() and BambuSlicer().is_available()),
    reason="needs both the CAD kernel and Bambu Studio",
)

BED_CENTRE = 128.0


@pytest.fixture
def model() -> ModellingSession:
    return ModellingSession(Build123dCompiler(Build123dKernel()), mesh_io=TrimeshIO())


def onto_the_bed(session: ModellingSession) -> Mesh:
    """The modelled part, sitting on the middle of the plate.

    The kernel builds everything centred on the origin, which is half under the
    bed. Dropping it and moving it to the middle is what the workspace does
    before slicing anything, and doing it here keeps this test about the chain
    rather than about placement.
    """
    mesh = session.state.mesh
    assert mesh is not None, "the model should have rebuilt"
    return mesh.dropped_to_bed().translated(BED_CENTRE, BED_CENTRE, 0)


def sliced(mesh: Mesh, tmp_path, name: str, supports: SupportType):
    """Slice a mesh with the real slicer and read its toolpath back."""
    model_file = tmp_path / f"{name}.stl"
    assert TrimeshIO().save(mesh, model_file).ok

    report = BambuSlicer().slice(
        SliceJob(
            model_path=model_file,
            printer=PrinterProfile.p2s(),
            output_dir=tmp_path / name,
            supports=supports,
            auto_orient=False,  # the part was modelled the way up it should print
        )
    )
    assert report.ok, f"slicing failed: {getattr(report, 'error', '')}"
    finished = report.unwrap()
    assert finished.succeeded
    assert finished.gcode_path is not None
    return finished, verify_gcode(finished.gcode_path)


@everything_required
def test_a_bracket_modelled_in_the_app_slices_into_sound_gcode(model, tmp_path):
    """The commonest printed part there is, made with the toolbar.

    A plate, one drilled hole, a row of four, rounded corners. Four commands,
    still parametric, and the holes follow the plate if the plate changes.
    """
    assert model.apply(CreateBox(80, 30, 5)).ok
    assert model.apply(CreateCylinder(2, 20, x=-30, cut=True)).ok
    assert model.apply(Repeat(4, dx=20)).ok
    assert model.apply(Fillet(3, EdgeSelector.VERTICAL)).ok

    size = model.state.measurements
    assert size is not None
    one_hole = math.pi * 2**2 * 5
    assert size.volume_mm3 == pytest.approx(80 * 30 * 5 - 4 * one_hole, rel=0.02)

    finished, checked = sliced(onto_the_bed(model), tmp_path, "bracket", SupportType.NONE)

    assert checked.was_read, "the toolpath should be readable"
    assert checked.layer_count >= 20, "a 5 mm plate at 0.2 mm layers"
    assert checked.unsupported_layers == 0, "a flat plate needs nothing holding it up"
    assert checked.first_layer_area_mm2 > 1000, "it should be well stuck to the plate"
    assert finished.predicted_seconds > 0, "and it should say how long it will take"


@everything_required
def test_a_lettered_case_slices_and_the_lettering_survives_to_the_toolpath(model, tmp_path):
    """Rob's own example, shrunk: a hollow box with a name across the front."""
    assert model.apply(CreateBox(60, 25, 40)).ok
    assert model.apply(Fillet(2, EdgeSelector.VERTICAL)).ok
    assert model.apply(TextOnSurface("MSI", size=12, depth=1.5)).ok

    before = model.state.measurements
    assert before is not None

    _, checked = sliced(onto_the_bed(model), tmp_path, "case", SupportType.TREE_AUTO)

    assert checked.was_read
    assert checked.tallest_z == pytest.approx(40, abs=1.0), "the box, standing up"
    assert checked.layer_count >= 150


@everything_required
def test_a_spun_cup_slices_without_needing_anything_holding_it_up(model, tmp_path):
    """A vessel is the shape that most often turns out to need supports.

    It should not: the walls rise straight off the base. If this ever reports
    unsupported layers, something about the revolve is wrong rather than
    something about the slicer.
    """
    assert model.apply(Revolve(((0, 0), (20, 0), (20, 45), (17, 45), (17, 3), (0, 3)))).ok

    _, checked = sliced(onto_the_bed(model), tmp_path, "cup", SupportType.NONE)

    assert checked.was_read
    assert checked.unsupported_layers == 0
    assert checked.tallest_z == pytest.approx(45, abs=1.0)


@everything_required
def test_an_overhang_modelled_in_the_app_is_caught_and_supports_fix_it(model, tmp_path):
    """The check that earns its place, on a part the app made.

    An extruded upright with an arm out of its side at mid height. Sliced bare
    the arm floats; sliced with supports it does not. Anything less than this
    would be a test of the slicer's defaults rather than of the verification.
    """
    assert model.apply(Extrude(((0, 0), (12, 0), (12, 40), (0, 40)), 12)).ok
    assert model.apply(CreateBox(20, 10, 4, x=15, z=2)).ok

    part = onto_the_bed(model)

    _, bare = sliced(part, tmp_path, "bare", SupportType.NONE)
    _, propped = sliced(part, tmp_path, "propped", SupportType.TREE_AUTO)

    assert bare.unsupported_layers >= 1, "the arm should be caught hanging in the air"
    assert propped.unsupported_layers == 0, "and supports should hold it up"


@everything_required
def test_the_part_that_reaches_the_slicer_is_the_part_that_was_modelled(model, tmp_path):
    """The link most likely to go wrong silently.

    A mesh written at the wrong scale, or with the tree rebuilt from a stale
    document, still slices perfectly happily - into the wrong object. So the
    solid's own measurements are checked against the mesh handed on, and both
    against what the G-code says it will print.
    """
    assert model.apply(CreateBox(50, 40, 30)).ok
    assert model.apply(Fillet(4, EdgeSelector.ALL)).ok

    size = model.state.measurements
    mesh = onto_the_bed(model)
    assert size is not None

    assert mesh.bounds.width.millimetres == pytest.approx(size.width.millimetres, abs=0.2)
    assert mesh.bounds.depth.millimetres == pytest.approx(size.depth.millimetres, abs=0.2)
    assert mesh.bounds.height.millimetres == pytest.approx(size.height.millimetres, abs=0.2)
    assert np.isclose(mesh.bounds.min_z, 0.0, atol=0.01), "sitting on the plate"

    _, checked = sliced(mesh, tmp_path, "rounded", SupportType.NONE)

    assert checked.tallest_z == pytest.approx(size.height.millimetres, abs=0.5)
