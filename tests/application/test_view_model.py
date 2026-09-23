"""The view-model, driven with no display, no event loop and no Qt.

That this file imports nothing from PySide6 is the whole point of the layering.
An import-linter contract enforces it, and this suite is the proof that it pays.
"""

from pathlib import Path

import pytest

from modelpop.application.workspace import Workspace
from modelpop.domain import Length
from modelpop.domain.printer import PrinterProfile, SupportType
from modelpop.domain.readiness import Severity
from modelpop.presentation import WorkspaceViewModel

from .test_workspace import FakeIO, FakeOps, FakeSlicer


def view_model(
    *,
    watertight: bool = True,
    overhangs: float = 0.0,
    load_error: str = "",
    slicer: FakeSlicer | None = None,
) -> WorkspaceViewModel:
    workspace = Workspace(
        FakeIO(load_error=load_error),
        FakeOps(watertight=watertight, overhangs=overhangs),
        slicer if slicer is not None else FakeSlicer(),
        PrinterProfile.p2s(),
    )
    return WorkspaceViewModel(workspace)


class TestObserving:
    def test_listeners_hear_about_a_new_model(self):
        vm = view_model()
        seen = []
        vm.on_state_changed(seen.append)
        vm.open(Path("model.stl"))
        assert len(seen) == 1
        assert seen[0].has_model

    def test_listeners_hear_a_notification_for_every_action(self):
        vm = view_model()
        notes = []
        vm.on_notification(notes.append)
        vm.open(Path("model.stl"))
        vm.prepare_for_bed()
        assert [n.message for n in notes] == ["Opened model.stl", "Placed on the bed"]

    def test_busy_is_raised_and_lowered_around_work(self):
        vm = view_model()
        states = []
        vm.on_busy_changed(states.append)
        vm.open(Path("model.stl"))
        assert states == [True, False]
        assert not vm.is_busy

    def test_busy_is_lowered_even_when_the_work_fails(self):
        vm = view_model(load_error="broken")
        vm.open(Path("model.stl"))
        assert not vm.is_busy


class TestNotifications:
    def test_a_failure_is_marked_so_the_ui_can_interrupt(self):
        vm = view_model(load_error="Could not read it")
        notes = []
        vm.on_notification(notes.append)
        vm.open(Path("nope.stl"))

        assert notes[0].is_error
        assert notes[0].severity is Severity.BLOCKER
        assert "Could not read it" in notes[0].detail

    def test_opening_a_broken_model_is_not_reported_as_a_failed_operation(self):
        """The open succeeded; the model is the problem.

        Interrupting with an error dialog here would be wrong - the user asked
        to open the file, and we did. The findings panel carries the bad news.
        """
        vm = view_model(watertight=False)
        notes = []
        vm.on_notification(notes.append)
        vm.open(Path("broken.stl"))

        assert not notes[0].is_error
        assert notes[0].severity is Severity.BLOCKER, "the model is still flagged"

    def test_a_slice_is_described_in_the_users_terms(self):
        vm = view_model(overhangs=0.3)
        notes = []
        vm.open(Path("model.stl"))
        vm.on_notification(notes.append)
        vm.slice(Path("out"))

        assert "10m of print time" in notes[-1].message
        assert "supports generated" in notes[-1].message


class TestCommandAvailability:
    def test_nothing_is_available_before_a_model_is_open(self):
        vm = view_model()
        assert not vm.can_repair
        assert not vm.can_slice

    def test_repair_is_offered_only_when_it_would_help(self):
        broken = view_model(watertight=False)
        broken.open(Path("broken.stl"))
        assert broken.can_repair

        healthy = view_model()
        healthy.open(Path("fine.stl"))
        assert not healthy.can_repair

    def test_slicing_is_offered_only_once_the_model_is_printable(self):
        vm = view_model(watertight=False)
        vm.open(Path("broken.stl"))
        assert not vm.can_slice

        vm.repair()
        assert vm.can_slice


class TestTheWholeJourney:
    def test_broken_download_to_sliced_print(self):
        """The Phase 2 milestone, in one test and with no display."""
        vm = view_model(watertight=False, overhangs=0.2)
        notes = []
        vm.on_notification(notes.append)

        vm.open(Path("downloaded.stl"))
        assert not vm.can_slice, "a broken download must not be printable"

        vm.repair()
        assert vm.can_slice

        vm.scale_to_fit(Length.inches(6))
        assert vm.state.mesh.bounds.largest_dimension.millimetres == pytest.approx(152.4)

        vm.prepare_for_bed()
        assert vm.state.mesh.bounds.min_z == pytest.approx(0.0)

        vm.slice(Path("out"))
        assert vm.state.last_slice is not None
        assert vm.state.last_slice.succeeded
        assert not any(n.is_error for n in notes)


class TestConcurrency:
    def test_a_second_command_is_refused_while_one_is_running(self):
        """The UI disables buttons, but the guard belongs here too.

        Driven through the runner seam: queueing the work instead of running it
        leaves the view-model busy, exactly as a worker thread would.
        """
        pending: list[object] = []
        workspace = Workspace(FakeIO(), FakeOps(), FakeSlicer(), PrinterProfile.p2s())
        vm = WorkspaceViewModel(workspace, runner=pending.append)
        notes = []
        vm.on_notification(notes.append)

        vm.open(Path("model.stl"))
        assert vm.is_busy and len(pending) == 1

        vm.open(Path("another.stl"))
        assert any("Already working" in n.message for n in notes)
        assert len(pending) == 1, "the second command must not have been queued"

    def test_the_runner_seam_lets_work_finish_later(self):
        pending: list = []
        workspace = Workspace(FakeIO(), FakeOps(), FakeSlicer(), PrinterProfile.p2s())
        vm = WorkspaceViewModel(workspace, runner=pending.append)

        vm.open(Path("model.stl"))
        assert not vm.state.has_model, "nothing has run yet"

        pending.pop()()
        assert vm.state.has_model
        assert not vm.is_busy


class TestSaving:
    def test_saving_reports_success(self, tmp_path):
        vm = view_model()
        vm.open(Path("model.stl"))
        notes = []
        vm.on_notification(notes.append)

        vm.save_as(tmp_path / "copy.stl")
        assert notes[-1].message.startswith("Saved to")
        assert not notes[-1].is_error

    def test_saving_with_no_model_is_reported_as_a_failure(self, tmp_path):
        vm = view_model()
        notes = []
        vm.on_notification(notes.append)
        vm.save_as(tmp_path / "nothing.stl")
        assert notes[-1].is_error


class TestSupportChoice:
    def test_the_view_model_leaves_the_support_decision_to_the_geometry(self):
        slicer = FakeSlicer()
        workspace = Workspace(FakeIO(), FakeOps(overhangs=0.0), slicer, PrinterProfile.p2s())
        vm = WorkspaceViewModel(workspace)
        vm.open(Path("model.stl"))
        vm.slice(Path("out"))
        assert slicer.jobs[-1].supports is SupportType.NONE
