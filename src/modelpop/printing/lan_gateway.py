"""Sending a job to a Bambu printer over the local network.

Two protocols, because the printer uses two. The file goes up over **FTPS**,
which is in the standard library; the instruction to start it goes over
**MQTT**, which is not, and is therefore an optional extra. Without it the file
still lands on the printer and the user starts it from its own screen - which
is a smaller thing than remote start, but is the difference between leaving the
application and not.

LAN mode only, deliberately. The cloud route needs a Bambu account, a token
refresh cycle and someone else's server in the middle of a print job, and this
application exists to avoid exactly that.

Nothing here raises. A printer that is off, on another subnet, or has had its
access code changed is an ordinary Tuesday.
"""

from __future__ import annotations

import ftplib
import json
import socket
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from modelpop.application.printer_ports import (
    PrinterState,
    PrinterStatus,
    Submission,
)
from modelpop.domain.result import Result, failure, success

if TYPE_CHECKING:
    from modelpop.application.printer_ports import PrintJob
    from modelpop.domain.printer import PrinterConnection

__all__ = [
    "ACCESS_CODE_NAME",
    "HOST_NAME",
    "SERIAL_NAME",
    "BambuLanGateway",
    "DryRunGateway",
    "mqtt_is_available",
    "read_status",
]

# All three live in the OS credential store. Only the access code is a
# secret; the other two are there because it is the one place this
# application persists anything, and splitting an address from the code
# that opens it across two mechanisms helps nobody.
ACCESS_CODE_NAME = "BAMBU_ACCESS_CODE"
HOST_NAME = "BAMBU_PRINTER_HOST"
SERIAL_NAME = "BAMBU_PRINTER_SERIAL"

# Implicit TLS on 990 and MQTT over TLS on 8883. Both are fixed in the
# printer's firmware; neither is configurable, so neither is a setting.
FTPS_PORT = 990
MQTT_PORT = 8883

# The printer's own account name for LAN mode. The password is the access code.
FTP_USER = "bblp"
MQTT_USER = "bblp"

# Where the printer looks for files it can print. The SD card root also works
# on current firmware, but this is what Bambu Studio itself uses.
REMOTE_DIRECTORY = "/cache"

# Long enough for a slow upload over wifi, short enough that a printer which is
# simply not there does not hang the worker for minutes.
CONNECT_TIMEOUT_SECONDS = 15.0
STATUS_TIMEOUT_SECONDS = 10.0


def mqtt_is_available() -> bool:
    """Whether the optional MQTT client is installed."""
    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        return False
    return True


class _ImplicitFtpTls(ftplib.FTP_TLS):
    """FTPS that is already encrypted when the connection opens.

    The printer speaks *implicit* TLS: the socket is wrapped before a single
    byte of FTP crosses it. ``ftplib`` only knows the explicit flavour, where
    it connects in the clear and issues ``AUTH TLS``, so the socket is wrapped
    here on the way past.

    Without this the connection does not fail cleanly - it hangs, because the
    printer is waiting for a TLS handshake and ``ftplib`` is waiting for a
    greeting banner.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Start with no wrapped socket."""
        super().__init__(*args, **kwargs)
        self._sock: socket.socket | None = None

    @property
    def sock(self) -> socket.socket | None:
        """The socket, wrapped in TLS."""
        return self._sock

    @sock.setter
    def sock(self, value: socket.socket | None) -> None:
        """Wrap every socket as it is assigned."""
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value


def _relaxed_tls() -> ssl.SSLContext:
    """A TLS context that accepts the printer's own certificate.

    The printer presents a self-signed certificate for an IP address, which no
    certificate authority will ever vouch for. Verification is therefore turned
    off - and that is worth being honest about rather than burying: this
    protects the access code from passive listeners on the local network, and
    does **not** prove the machine at that address is the printer.

    It is the same trade Bambu Studio makes, on a network the user controls.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


@dataclass(frozen=True, slots=True)
class DryRunGateway:
    """Reports what it would have sent, and sends nothing.

    The default, and the reason the send path can be wired up and exercised
    end to end without a printer in the room. A print is the one irreversible
    thing this application does, so the harmless implementation is the one you
    get unless you ask for the other.
    """

    def is_available(self) -> bool:
        """Always, because it needs nothing."""
        return True

    def describe(self) -> str:
        """What this gateway does."""
        return "Dry run - jobs are described, not sent"

    def send(self, job: PrintJob) -> Result[Submission]:
        """Check the job over, then decline to send it."""
        problem = job.problem
        if problem is not None:
            return failure("That job cannot be sent", problem)
        return success(Submission(filename=job.filename, was_dry_run=True))

    def status(self, connection: PrinterConnection) -> Result[PrinterStatus]:
        """No printer was asked, so nothing is known."""
        return success(PrinterStatus(detail="Dry run: the printer was not contacted."))


class BambuLanGateway:
    """Uploads over FTPS, and starts the print over MQTT when it can."""

    def __init__(self, *, timeout: float = CONNECT_TIMEOUT_SECONDS) -> None:
        """Create the gateway."""
        self._timeout = timeout

    def is_available(self) -> bool:
        """Always: uploading needs only the standard library."""
        return True

    def describe(self) -> str:
        """How jobs would be sent, and what is missing if anything is."""
        if mqtt_is_available():
            return "Over the local network: FTPS upload, MQTT start"
        return (
            "Over the local network: FTPS upload only. Install the 'printer' "
            "extra to start prints remotely."
        )

    # ------------------------------------------------------------------ send

    def send(self, job: PrintJob) -> Result[Submission]:
        """Put the file on the printer, then start it only if asked to."""
        problem = job.problem
        if problem is not None:
            return failure("That job cannot be sent", problem)

        uploaded = self._upload(job)
        if not uploaded.ok:
            return uploaded  # type: ignore[return-value]

        if not job.start_now:
            return success(Submission(uploaded=True, filename=job.filename))

        if not mqtt_is_available():
            # The file is there. Said plainly rather than reported as a
            # failure, because the user's model is on the printer either way
            # and telling them it failed would send them to look for it.
            return success(
                Submission(
                    uploaded=True,
                    filename=job.filename,
                    detail=(
                        "The print was not started: that needs the optional MQTT "
                        "client. Start it from the printer's screen."
                    ),
                )
            )

        started = self._start(job)
        if not started.ok:
            return success(Submission(uploaded=True, filename=job.filename, detail=started.error))
        return success(Submission(uploaded=True, started=True, filename=job.filename))

    def _upload(self, job: PrintJob) -> Result[str]:
        """Copy the file onto the printer over FTPS."""
        connection = job.connection
        try:
            ftp = _ImplicitFtpTls(context=_relaxed_tls(), timeout=self._timeout)
            ftp.connect(host=connection.host, port=FTPS_PORT, timeout=self._timeout)
            ftp.login(user=FTP_USER, passwd=connection.access_code)
            ftp.prot_p()  # encrypt the data channel too, not just the commands
            with job.file_path.open("rb") as payload:
                ftp.storbinary(f"STOR {REMOTE_DIRECTORY}/{job.filename}", payload)
            ftp.quit()
        except ftplib.error_perm as denied:
            return failure(
                "The printer refused the file",
                f"{denied}. Check the access code on the printer's network screen.",
            )
        except (OSError, ftplib.all_errors) as unreachable:  # type: ignore[misc]
            return failure(
                "The printer could not be reached",
                f"{connection.host} did not answer on port {FTPS_PORT}: {unreachable}",
            )
        return success(job.filename)

    def _start(self, job: PrintJob) -> Result[str]:
        """Tell the printer to print the file that was just uploaded."""
        command = {
            "print": {
                "sequence_id": "0",
                "command": "project_file",
                "param": f"Metadata/plate_{job.plate}.gcode",
                "url": f"file:///mnt/sdcard{REMOTE_DIRECTORY}/{job.filename}",
                "bed_type": "auto",
                "bed_levelling": job.bed_levelling,
                "flow_cali": job.flow_calibration,
                "vibration_cali": True,
                "layer_inspect": False,
                "use_ams": False,
                "task_id": "0",
                "subtask_id": "0",
                "subtask_name": job.filename,
                "project_id": "0",
                "profile_id": "0",
            }
        }
        return _publish(job.connection, command, self._timeout)

    # ---------------------------------------------------------------- status

    def status(self, connection: PrinterConnection) -> Result[PrinterStatus]:
        """Ask the printer what it is doing."""
        if not mqtt_is_available():
            return success(
                PrinterStatus(detail="Reading the printer's status needs the optional MQTT client.")
            )
        problem = connection.problem
        if problem is not None:
            return failure("The printer is not set up", problem)
        return _listen(connection, STATUS_TIMEOUT_SECONDS)


# ------------------------------------------------------------------ mqtt


def _client(connection: PrinterConnection) -> Any:
    """A connected MQTT client, or an exception.

    Kept apart from the two things that use it so the connection ceremony -
    which is identical for publishing and for listening - exists once.
    """
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, connection.access_code)
    client.tls_set_context(_relaxed_tls())
    client.tls_insecure_set(True)
    return client


def _publish(connection: PrinterConnection, payload: dict[str, Any], timeout: float) -> Result[str]:
    """Send one command to the printer and wait for it to go out."""
    try:
        client = _client(connection)
        client.connect(connection.host, MQTT_PORT, keepalive=int(timeout))
        client.loop_start()
        try:
            message = client.publish(
                f"device/{connection.serial}/request", json.dumps(payload), qos=1
            )
            message.wait_for_publish(timeout)
            delivered = message.is_published()
        finally:
            client.loop_stop()
            client.disconnect()
    except (OSError, ValueError, RuntimeError) as failed:
        return failure(
            "The printer accepted the file but would not start it",
            f"{type(failed).__name__}: {failed}",
        )

    if not delivered:
        return failure(
            "The printer accepted the file but would not start it",
            "It did not acknowledge the instruction within the timeout.",
        )
    return success("started")


def _listen(connection: PrinterConnection, timeout: float) -> Result[PrinterStatus]:
    """Subscribe until the printer reports its state, or the time runs out.

    The printer publishes a full report when asked and deltas after that, so
    the first message is the one worth waiting for. ``pushall`` asks for it
    rather than waiting for the printer to get round to one on its own.
    """
    import threading

    heard: list[dict[str, Any]] = []
    arrived = threading.Event()

    def on_message(_client: Any, _data: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and "print" in payload:
            heard.append(payload["print"])
            arrived.set()

    try:
        client = _client(connection)
        client.on_message = on_message
        client.connect(connection.host, MQTT_PORT, keepalive=int(timeout))
        client.subscribe(f"device/{connection.serial}/report", qos=0)
        client.loop_start()
        try:
            client.publish(
                f"device/{connection.serial}/request",
                json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}),
                qos=1,
            )
            arrived.wait(timeout)
        finally:
            client.loop_stop()
            client.disconnect()
    except (OSError, ValueError, RuntimeError) as failed:
        return success(
            PrinterStatus(
                state=PrinterState.UNREACHABLE,
                detail=f"{connection.host}: {type(failed).__name__}: {failed}",
            )
        )

    if not heard:
        return success(
            PrinterStatus(
                state=PrinterState.UNREACHABLE,
                detail=f"{connection.host} did not report within {timeout:.0f} seconds.",
            )
        )
    return success(read_status(heard[-1]))


# The printer's own vocabulary for what it is doing. Anything not listed is
# reported as unknown rather than guessed at, because a wrong guess here says
# "idle" about a machine that is mid-print.
_STATES = {
    "IDLE": PrinterState.IDLE,
    "RUNNING": PrinterState.PRINTING,
    "PREPARE": PrinterState.PRINTING,
    "SLICING": PrinterState.PRINTING,
    "PAUSE": PrinterState.PAUSED,
    "FINISH": PrinterState.FINISHED,
    "FAILED": PrinterState.FAILED,
}


def read_status(report: dict[str, Any]) -> PrinterStatus:
    """Turn one of the printer's reports into something the app understands.

    A pure function, so every shape of report - including the truncated ones
    the printer sends as deltas, and the malformed ones it sends when it is
    unhappy - is testable without a printer.
    """
    return PrinterStatus(
        state=_STATES.get(str(report.get("gcode_state", "")).upper(), PrinterState.UNKNOWN),
        job_name=str(report.get("subtask_name", "") or report.get("gcode_file", "")),
        percent_done=_number(report.get("mc_percent")),
        minutes_remaining=int(_number(report.get("mc_remaining_time"))),
        nozzle_celsius=_number(report.get("nozzle_temper")),
        bed_celsius=_number(report.get("bed_temper")),
        nozzle_mm=_number(report.get("nozzle_diameter")),
        model_id=_model_id(report),
    )


def _model_id(report: dict[str, Any]) -> str:
    """The printer's code for itself, if this report carries one.

    Several keys are tried because Bambu has not put it in the same place in
    every firmware, and a printer that does not say is an ordinary outcome -
    the configured model answers for it. Only *recognised* codes matter
    downstream, so a stray string here costs nothing: the catalogue simply
    does not know it and the setting stands.
    """
    for key in ("printer_type", "dev_model_name", "model_id", "device_model"):
        said = report.get(key)
        if isinstance(said, str) and said.strip():
            return said.strip()
    return ""


def _number(value: Any) -> float:
    """A number from a report field that may be missing, null or a string."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
