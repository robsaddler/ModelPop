"""Growing thin walls until the nozzle can lay them down.

"Getting thinnest wall errors on the dragon. Fine. But how do I select the
dragon to make walls thicker? Can't we get an auto-correct here to add that
thickness inside where the user can't see it?"

Inside is the right instinct and not quite the mechanism. A thin wall on a
solid model is thin all the way through - there is no cavity to pad. What there
is, is room to move both of its faces apart by a fraction of a millimetre,
which thickens the wall by twice that and changes nothing anybody can see.

The loop is rounds rather than arithmetic because wall thickness is *sampled*:
the figure comes from two thousand rays fired through the surface, so it moves
a little each time it is taken. Grow, re-measure, grow again.
"""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import trimesh

from modelpop.application.workspace import MOST_GROWTH_MM, Workspace
from modelpop.domain.mesh import Mesh
from modelpop.domain.printer import PrinterProfile
from modelpop.domain.units import Length
from modelpop.mesh import TrimeshIO, TrimeshOps


def a_slab(thickness_mm: float) -> Mesh:
    """A flat plate, whose wall thickness is simply how thick it is."""
    box = trimesh.creation.box(extents=(40.0, 40.0, thickness_mm))
    box.apply_translation([0.0, 0.0, thickness_mm / 2])
    return Mesh(np.asarray(box.vertices), np.asarray(box.faces, np.int32))


def workspace() -> Workspace:
    return Workspace(TrimeshIO(), TrimeshOps(), printer=PrinterProfile.p2s())


class TestTheAdapterGrowsTheSurface:
    def test_a_wall_gains_twice_what_each_face_moves(self):
        """Both faces move outwards, so the wall gains 2x the offset."""
        ops = TrimeshOps()
        grown = ops.thicken(a_slab(1.0), Length.mm(0.25)).unwrap()

        assert grown.bounds.height.millimetres == pytest.approx(1.5, abs=0.02)

    def test_it_stays_a_solid(self):
        ops = TrimeshOps()
        grown = ops.thicken(a_slab(1.0), Length.mm(0.25)).unwrap()

        assert ops.inspect(grown, measure_walls=False).is_watertight

    def test_the_triangles_are_untouched(self):
        """Only the vertices move. Nothing is resampled, so no detail is lost.

        This is the whole reason not to reach for the voxel remesh here: that
        rebuilds the surface from a grid and turns fine relief into banding.
        """
        ops = TrimeshOps()
        before = a_slab(1.0)
        grown = ops.thicken(before, Length.mm(0.25)).unwrap()

        assert grown.triangle_count == before.triangle_count
        assert np.array_equal(grown.faces, before.faces)

    def test_growing_by_nothing_changes_nothing(self):
        before = a_slab(1.0)
        assert TrimeshOps().thicken(before, Length.mm(0.0)).unwrap() == before

    def test_an_empty_mesh_is_refused(self):
        assert not TrimeshOps().thicken(Mesh.empty(), Length.mm(0.2)).ok


class TestGrowingUntilItIsPrintable:
    def test_a_thin_slab_comes_back_thick_enough(self):
        space = workspace()
        target = PrinterProfile.p2s().nozzle.minimum_wall
        state = space.adopt(a_slab(0.5))
        assert state.readiness is not None

        result = space.thicken_until_printable(state)

        assert result.ok, result.error
        walls = TrimeshOps().inspect(result.unwrap().mesh).thinnest_wall
        assert walls is not None
        assert walls >= target, f"still {walls.format()}, wanted {target.format()}"

    def test_the_warning_it_was_answering_goes_away(self):
        """The readiness panel is where the user sees whether it worked."""
        space = workspace()
        state = space.adopt(a_slab(0.5))

        after = space.thicken_until_printable(state).unwrap()

        assert after.readiness is not None
        assert not [f for f in after.readiness.findings if f.fix_stage == "thickness"]

    def test_the_model_barely_changes_size(self):
        """ "Where the user can't see it" is the requirement, and it is testable."""
        space = workspace()
        before = a_slab(0.5)
        after = space.thicken_until_printable(space.adopt(before)).unwrap().mesh

        grew = after.bounds.width.millimetres - before.bounds.width.millimetres
        assert 0.0 < grew <= MOST_GROWTH_MM * 2, f"it grew {grew:.2f} mm across"

    def test_walls_that_are_already_thick_enough_are_left_alone(self):
        space = workspace()
        result = space.thicken_until_printable(space.adopt(a_slab(6.0)))

        assert not result.ok
        assert "already thick enough" in result.error

    def test_a_model_that_is_not_closed_is_refused_with_a_reason(self):
        """Thickness is measured by firing rays through a solid."""
        space = workspace()
        box = trimesh.creation.box(extents=(20.0, 20.0, 1.0))
        open_mesh = Mesh(np.asarray(box.vertices), np.asarray(box.faces[2:], np.int32))

        result = space.thicken_until_printable(space.adopt(open_mesh))

        assert not result.ok
        assert "closed first" in result.error
        assert "Repair it" in result.error

    def test_nothing_open_is_refused(self):
        space = workspace()
        result = space.thicken_until_printable(replace(space.adopt(a_slab(1.0)), mesh=None))

        assert not result.ok
        assert "no model is open" in result.error


class TestItIsOnlyOfferedWhenItHelps:
    def view(self, mesh: Mesh):
        from modelpop.presentation.workspace_view_model import WorkspaceViewModel

        view = WorkspaceViewModel(workspace())
        view.adopt(mesh)
        return view

    def test_offered_when_a_wall_is_too_thin(self):
        assert self.view(a_slab(0.5)).can_thicken

    def test_not_offered_when_everything_is_thick_enough(self):
        assert not self.view(a_slab(6.0)).can_thicken

    def test_not_offered_with_nothing_open(self):
        from modelpop.presentation.workspace_view_model import WorkspaceViewModel

        assert not WorkspaceViewModel(workspace()).can_thicken


@pytest.mark.integration
class TestTheRealDragon:
    """The model this was asked for, at 1.1 million triangles."""

    def dragon(self) -> Mesh | None:
        """The largest cached model that is actually a closed solid.

        Largest alone is not enough: the scene cache holds the same model from
        before it was repaired as well as after, and thickness cannot be
        measured on something with holes in it.
        """
        import os

        cached = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp"
        found = sorted(
            cached.glob("modelpop-scene-*/body-1-*.stl"), key=lambda p: -p.stat().st_size
        )
        ops = TrimeshIO(), TrimeshOps()
        for path in found[:6]:
            if path.stat().st_size < 10_000_000:
                break
            loaded = ops[0].load(path)
            if loaded.ok and ops[1].inspect(loaded.unwrap(), measure_walls=False).is_watertight:
                return loaded.unwrap()
        return None

    def test_it_becomes_printable_without_visibly_changing(self):
        mesh = self.dragon()
        if mesh is None:
            pytest.skip("no large closed model cached to test against")

        space = workspace()
        state = space.adopt(mesh)
        before = TrimeshOps().inspect(mesh).thinnest_wall
        assert before is not None

        after_state = space.thicken_until_printable(state).unwrap()
        after = TrimeshOps().inspect(after_state.mesh).thinnest_wall

        assert after is not None
        assert after >= PrinterProfile.p2s().nozzle.minimum_wall
        grew = after_state.mesh.bounds.width.millimetres - mesh.bounds.width.millimetres
        assert grew < 1.0, f"the model grew {grew:.2f} mm across, which is visible"
