"""Sending a finished job to the printer.

A send is the only thing in this application that does something physical, so
the tests are weighted towards what it *refuses* to do. Nothing here touches a
network: the FTPS and MQTT paths are exercised against a real printer in the
integration suite, which skips when there is not one, and everything that can
be decided without a socket is decided in a pure function and tested here.
"""

from pathlib import Path

import pytest

from modelpop.application.printer_ports import (
    PrinterState,
    PrinterStatus,
    PrintJob,
    Submission,
)
from modelpop.application.workspace import Workspace, WorkspaceState
from modelpop.domain.printer import PrinterConnection
from modelpop.domain.readiness import Severity
from modelpop.presentation.workspace_view_model import Notification, WorkspaceViewModel
from modelpop.printing.lan_gateway import DryRunGateway, read_status

REACHABLE = PrinterConnection(host="192.168.1.50", serial="01P00A1234567", access_code="12345678")


@pytest.fixture
def sliced_file(tmp_path: Path) -> Path:
    target = tmp_path / "bracket.gcode.3mf"
    target.write_bytes(b"not really a 3mf")
    return target


class TestTheConnection:
    def test_the_access_code_never_appears_in_a_repr(self):
        """This object lands in tracebacks and log lines. The code must not."""
        shown = repr(REACHABLE)
        assert "12345678" not in shown
        assert "192.168.1.50" in shown, "the address is not a secret and helps"

    def test_the_access_code_never_appears_in_the_description(self):
        assert "12345678" not in REACHABLE.describe()

    def test_it_is_complete_when_all_three_are_there(self):
        assert REACHABLE.is_complete
        assert REACHABLE.problem is None

    def test_copied_and_pasted_spaces_are_trimmed(self):
        """Every one of these gets pasted from a phone screen or a note."""
        typed = PrinterConnection(host=" 192.168.1.50 ", serial="X1\n", access_code=" 12345678 ")
        assert typed.host == "192.168.1.50"
        assert typed.access_code == "12345678"

    @pytest.mark.parametrize(
        ("connection", "expected"),
        [
            (PrinterConnection(), "address"),
            (PrinterConnection(host="10.0.0.5"), "serial"),
            (PrinterConnection(host="10.0.0.5", serial="X1"), "access code"),
            (
                PrinterConnection(host="10.0.0.5", serial="X1", access_code="123"),
                "8 characters",
            ),
        ],
    )
    def test_what_is_missing_is_named(self, connection, expected):
        assert expected in (connection.problem or "")


class TestTheJob:
    def test_the_name_on_the_printer_comes_from_the_file(self, sliced_file):
        job = PrintJob(file_path=sliced_file, connection=REACHABLE)
        assert job.filename == "bracket.gcode.3mf"

    def test_a_chosen_name_wins(self, sliced_file):
        """A printer's file list is a flat list on a small screen."""
        job = PrintJob(file_path=sliced_file, connection=REACHABLE, name="shelf pin")
        assert job.filename == "shelf pin.3mf"

    def test_a_name_full_of_punctuation_is_made_safe(self, sliced_file):
        job = PrintJob(file_path=sliced_file, connection=REACHABLE, name="../../etc/passwd")
        assert "/" not in job.filename
        assert ".." not in job.filename

    def test_a_name_with_nothing_usable_in_it_still_produces_a_file(self, sliced_file):
        job = PrintJob(file_path=sliced_file, connection=REACHABLE, name="???")
        assert job.filename.startswith("modelpop")

    def test_starting_immediately_is_not_the_default(self, sliced_file):
        """Uploading is reversible. Starting a print is not."""
        assert not PrintJob(file_path=sliced_file, connection=REACHABLE).start_now

    def test_a_missing_file_is_caught_before_any_connection(self, tmp_path):
        job = PrintJob(file_path=tmp_path / "gone.3mf", connection=REACHABLE)
        assert "not there any more" in (job.problem or "")

    def test_a_half_configured_printer_is_caught_before_any_connection(self, sliced_file):
        job = PrintJob(file_path=sliced_file, connection=PrinterConnection(host="10.0.0.5"))
        assert "serial" in (job.problem or "")


class TestDryRunning:
    """The default gateway, and the reason the whole path is exercisable."""

    def test_it_sends_nothing_and_says_so(self, sliced_file):
        outcome = DryRunGateway().send(PrintJob(file_path=sliced_file, connection=REACHABLE))

        assert outcome.ok
        submission = outcome.unwrap()
        assert submission.was_dry_run
        assert not submission.uploaded
        assert "not sent" in submission.describe()

    def test_it_still_checks_the_job_over(self, tmp_path):
        """A dry run that accepted an impossible job would teach nothing."""
        outcome = DryRunGateway().send(
            PrintJob(file_path=tmp_path / "gone.3mf", connection=REACHABLE)
        )
        assert not outcome.ok

    def test_it_never_claims_to_know_what_the_printer_is_doing(self):
        status = DryRunGateway().status(REACHABLE).unwrap()
        assert status.state is PrinterState.UNKNOWN


class TestWhatTheUserIsTold:
    def test_uploaded_and_started_reads_as_finished(self):
        told = Submission(uploaded=True, started=True, filename="a.3mf").describe()
        assert "sent" in told
        assert "started" in told

    def test_uploaded_but_not_started_says_where_to_go_next(self):
        """The model is on the printer either way; the user needs to know that."""
        told = Submission(uploaded=True, filename="a.3mf").describe()
        assert "on the printer" in told
        assert "printer's screen" in told

    def test_a_job_that_did_not_arrive_says_why(self):
        told = Submission(filename="a.3mf", detail="the printer refused it").describe()
        assert "not sent" in told
        assert "refused" in told

    def test_a_busy_printer_reports_its_progress(self):
        told = PrinterStatus(
            state=PrinterState.PRINTING,
            job_name="bracket",
            percent_done=42.0,
            minutes_remaining=31,
        ).describe()
        assert "42%" in told
        assert "31 min left" in told

    def test_an_unreachable_printer_says_so_rather_than_guessing_idle(self):
        told = PrinterStatus(state=PrinterState.UNREACHABLE, detail="timed out").describe()
        assert "did not answer" in told

    @pytest.mark.parametrize(
        ("state", "busy"),
        [
            (PrinterState.PRINTING, True),
            (PrinterState.PAUSED, True),
            (PrinterState.IDLE, False),
            (PrinterState.FINISHED, False),
            (PrinterState.UNKNOWN, False),
        ],
    )
    def test_it_knows_when_sending_another_job_would_be_a_mistake(self, state, busy):
        assert state.is_busy is busy


class TestReadingTheReport:
    """The printer's own messages, turned into something the app understands."""

    def test_a_full_report_is_read(self):
        status = read_status(
            {
                "gcode_state": "RUNNING",
                "subtask_name": "bracket.3mf",
                "mc_percent": 42,
                "mc_remaining_time": 31,
                "nozzle_temper": 219.5,
                "bed_temper": 60.0,
            }
        )
        assert status.state is PrinterState.PRINTING
        assert status.job_name == "bracket.3mf"
        assert status.percent_done == pytest.approx(42.0)
        assert status.nozzle_celsius == pytest.approx(219.5)

    def test_a_delta_with_almost_nothing_in_it_does_not_fall_over(self):
        """Most of what the printer sends after the first report is a fragment."""
        assert read_status({"mc_percent": 7}).state is PrinterState.UNKNOWN

    def test_a_state_this_build_does_not_know_is_unknown_rather_than_idle(self):
        """Guessing "idle" about a machine mid-print is the worst wrong answer."""
        assert read_status({"gcode_state": "SOMETHING_NEW"}).state is PrinterState.UNKNOWN

    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            ("IDLE", PrinterState.IDLE),
            ("RUNNING", PrinterState.PRINTING),
            ("PREPARE", PrinterState.PRINTING),
            ("PAUSE", PrinterState.PAUSED),
            ("FINISH", PrinterState.FINISHED),
            ("FAILED", PrinterState.FAILED),
        ],
    )
    def test_every_state_the_printer_uses_is_understood(self, reported, expected):
        assert read_status({"gcode_state": reported}).state is expected

    def test_a_field_that_arrives_as_null_reads_as_zero(self):
        assert read_status({"mc_percent": None, "bed_temper": "warm"}).percent_done == 0.0


class Recording:
    """A gateway that records the job instead of sending it."""

    def __init__(self, outcome: Submission | None = None) -> None:
        self.jobs: list[PrintJob] = []
        self._outcome = outcome or Submission(uploaded=True, filename="a.3mf")

    def is_available(self) -> bool:
        return True

    def describe(self) -> str:
        return "a recording gateway"

    def send(self, job: PrintJob):
        from modelpop.domain.result import success

        self.jobs.append(job)
        return success(self._outcome)

    def status(self, connection: PrinterConnection):
        from modelpop.domain.result import success

        return success(PrinterStatus(state=PrinterState.IDLE))


class TestSendingFromTheWorkspace:
    def workspace(self, gateway=None) -> Workspace:
        from .test_workspace import FakeIO, FakeOps

        return Workspace(mesh_io=FakeIO(), mesh_ops=FakeOps(), printer_gateway=gateway)

    def test_nothing_can_be_sent_before_anything_is_sliced(self):
        gateway = Recording()
        outcome = self.workspace(gateway).send_to_printer(WorkspaceState(), REACHABLE)

        assert not outcome.ok
        assert "Nothing has been sliced" in outcome.error
        assert gateway.jobs == [], "it reached the gateway anyway"

    def test_with_no_gateway_it_says_the_printer_is_not_set_up(self):
        outcome = self.workspace().send_to_printer(WorkspaceState(), REACHABLE)
        assert not outcome.ok
        assert "No printer is set up" in outcome.error

    def test_it_sends_the_project_file_rather_than_the_bare_gcode(self, tmp_path):
        """The project carries the plate and the filament; G-code alone does not."""
        from modelpop.application.ports import SliceReport

        gcode = tmp_path / "plate.gcode"
        project = tmp_path / "plate.gcode.3mf"
        for path in (gcode, project):
            path.write_bytes(b"x")

        gateway = Recording()
        state = WorkspaceState(
            last_slice=SliceReport(True, "ok", gcode_path=gcode, project_path=project)
        )
        assert self.workspace(gateway).send_to_printer(state, REACHABLE, for_real=True).ok
        assert gateway.jobs[0].file_path == project

    def test_it_falls_back_to_the_gcode_when_there_is_no_project(self, tmp_path):
        from modelpop.application.ports import SliceReport

        gcode = tmp_path / "plate.gcode"
        gcode.write_bytes(b"x")

        gateway = Recording()
        state = WorkspaceState(last_slice=SliceReport(True, "ok", gcode_path=gcode))
        assert self.workspace(gateway).send_to_printer(state, REACHABLE, for_real=True).ok
        assert gateway.jobs[0].file_path == gcode

    def test_starting_is_off_unless_it_is_asked_for(self, tmp_path):
        from modelpop.application.ports import SliceReport

        gcode = tmp_path / "plate.gcode"
        gcode.write_bytes(b"x")
        gateway = Recording()
        state = WorkspaceState(last_slice=SliceReport(True, "ok", gcode_path=gcode))

        self.workspace(gateway).send_to_printer(state, REACHABLE, for_real=True)
        assert not gateway.jobs[0].start_now

        self.workspace(gateway).send_to_printer(state, REACHABLE, start_now=True, for_real=True)
        assert gateway.jobs[1].start_now

    def test_nothing_reaches_the_gateway_unless_it_is_asked_for_explicitly(self, tmp_path):
        """The guard that matters. Everything else here is downstream of it.

        The first attempt put this switch in the adapter, where the window's
        dry-run tick governed only which dialog appeared and an untouched
        setting still uploaded. Refused in the use case instead, there is no
        arrangement of gateways that sends an unasked-for job.
        """
        from modelpop.application.ports import SliceReport

        gcode = tmp_path / "plate.gcode"
        gcode.write_bytes(b"x")
        gateway = Recording()
        state = WorkspaceState(last_slice=SliceReport(True, "ok", gcode_path=gcode))

        outcome = self.workspace(gateway).send_to_printer(state, REACHABLE)

        assert outcome.ok
        assert outcome.unwrap().was_dry_run
        assert gateway.jobs == [], "the real gateway was reached on a dry run"

    def test_a_dry_run_still_refuses_a_job_that_could_not_work(self, tmp_path):
        """A dry run that accepted an impossible job would teach the wrong thing."""
        from modelpop.application.ports import SliceReport

        gone = tmp_path / "never-written.gcode"
        state = WorkspaceState(last_slice=SliceReport(True, "ok", gcode_path=gone))

        assert not self.workspace(Recording()).send_to_printer(state, REACHABLE).ok

    def test_a_dry_run_still_names_the_file_it_would_have_sent(self, tmp_path):
        from modelpop.application.ports import SliceReport

        gcode = tmp_path / "bracket.gcode"
        gcode.write_bytes(b"x")
        state = WorkspaceState(last_slice=SliceReport(True, "ok", gcode_path=gcode))

        submission = self.workspace(Recording()).send_to_printer(state, REACHABLE).unwrap()
        assert "bracket" in submission.describe()


class TestSendingFromTheViewModel:
    def model(self, gateway) -> tuple[WorkspaceViewModel, list[Notification]]:
        from .test_workspace import FakeIO, FakeOps

        view = WorkspaceViewModel(
            Workspace(mesh_io=FakeIO(), mesh_ops=FakeOps(), printer_gateway=gateway)
        )
        view.printer_connection = REACHABLE
        seen: list[Notification] = []
        view.on_notification(seen.append)
        return view, seen

    def sliced(self, view, tmp_path) -> None:
        from dataclasses import replace

        from modelpop.application.ports import SliceReport

        gcode = tmp_path / "plate.gcode"
        gcode.write_bytes(b"x")
        view._state = replace(view.state, last_slice=SliceReport(True, "ok", gcode_path=gcode))

    def test_sending_is_offered_only_once_something_is_sliced(self, tmp_path):
        view, _ = self.model(Recording())
        assert not view.can_send_to_printer

        self.sliced(view, tmp_path)
        assert view.can_send_to_printer

    def test_a_job_that_arrived_is_reported_as_news_not_as_an_error(self, tmp_path):
        """The model is on the printer. An error dialog would be wrong."""
        view, seen = self.model(Recording())
        self.sliced(view, tmp_path)
        view.send_to_printer(for_real=True)

        assert seen[-1].severity is Severity.INFO
        assert not seen[-1].is_error

    def test_a_job_that_did_not_arrive_is_reported_as_a_failure(self, tmp_path):
        view, seen = self.model(Recording(Submission(filename="a.3mf", detail="refused")))
        self.sliced(view, tmp_path)
        view.send_to_printer(for_real=True)

        assert seen[-1].severity is Severity.WARNING

    def test_a_printer_that_is_not_set_up_fails_loudly(self, tmp_path):
        view, seen = self.model(None)
        self.sliced(view, tmp_path)
        view.send_to_printer()

        assert seen[-1].is_error

    def test_the_connection_the_user_typed_is_the_one_used(self, tmp_path):
        gateway = Recording()
        view, _ = self.model(gateway)
        self.sliced(view, tmp_path)
        view.printer_connection = REACHABLE
        view.send_to_printer(for_real=True)

        assert gateway.jobs[0].connection == REACHABLE

    def test_the_status_can_be_asked_for(self, tmp_path):
        view, seen = self.model(Recording())
        view.read_printer_status()

        assert "idle" in seen[-1].message

    def test_the_view_model_sends_nothing_unless_it_is_asked_for_explicitly(self, tmp_path):
        gateway = Recording()
        view, seen = self.model(gateway)
        self.sliced(view, tmp_path)
        view.printer_connection = REACHABLE
        view.send_to_printer()

        assert gateway.jobs == []
        assert "not sent" in seen[-1].message
