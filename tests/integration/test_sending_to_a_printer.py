"""The two network paths, against a real printer.

Skipped unless one is configured, which is almost always. Nothing here can be
faked usefully: an FTPS server that is not a Bambu printer would prove that
``ftplib`` works, which was never in doubt, and the things that actually go
wrong - implicit TLS, the self-signed certificate, which directory the printer
looks in - only go wrong against the real machine.

To run these, set the three environment variables and pass ``-m integration``::

    MODELPOP_PRINTER_HOST=192.168.1.50
    MODELPOP_PRINTER_SERIAL=01P00A000000000
    MODELPOP_PRINTER_ACCESS_CODE=12345678

**Nothing here starts a print.** The upload test writes a small file and the
status test only reads. Starting a print is the one thing this suite will not
do on its own, because a test that can begin a physical print is a test nobody
should be able to run by accident.
"""

import os

import pytest

from modelpop.application.printer_ports import PrinterState, PrintJob
from modelpop.domain.printer import PrinterConnection
from modelpop.printing.lan_gateway import BambuLanGateway, mqtt_is_available

pytestmark = pytest.mark.integration


def configured() -> PrinterConnection:
    return PrinterConnection(
        host=os.environ.get("MODELPOP_PRINTER_HOST", ""),
        serial=os.environ.get("MODELPOP_PRINTER_SERIAL", ""),
        access_code=os.environ.get("MODELPOP_PRINTER_ACCESS_CODE", ""),
    )


printer_required = pytest.mark.skipif(
    not configured().is_complete,
    reason="no printer configured; set MODELPOP_PRINTER_HOST, _SERIAL and _ACCESS_CODE",
)


@printer_required
def test_a_file_can_be_put_on_the_printer(tmp_path):
    """The whole point of the FTPS path, and the only way to know it works.

    Implicit TLS, a self-signed certificate and the printer's own directory:
    each of those fails differently, and none of them fails against anything
    that is not a Bambu printer.
    """
    payload = tmp_path / "modelpop-connection-test.gcode.3mf"
    payload.write_bytes(b"ModelPop connection test. Safe to delete.")

    outcome = BambuLanGateway().send(
        PrintJob(file_path=payload, connection=configured(), name="modelpop test")
    )

    assert outcome.ok, getattr(outcome, "error", "")
    submission = outcome.unwrap()
    assert submission.uploaded
    assert not submission.started, "an upload must never start a print on its own"


@printer_required
def test_a_wrong_access_code_is_refused_rather_than_hanging(tmp_path):
    """The commonest real failure, and it must come back as a message."""
    payload = tmp_path / "modelpop-connection-test.gcode.3mf"
    payload.write_bytes(b"x")

    outcome = BambuLanGateway(timeout=10.0).send(
        PrintJob(
            file_path=payload,
            connection=PrinterConnection(
                host=configured().host, serial=configured().serial, access_code="00000000"
            ),
        )
    )

    assert not outcome.ok
    assert "access code" in outcome.error.lower() or "refused" in outcome.error.lower()


@printer_required
@pytest.mark.skipif(not mqtt_is_available(), reason="the optional MQTT client is not installed")
def test_the_printer_says_what_it_is_doing():
    status = BambuLanGateway().status(configured())

    assert status.ok
    assert status.unwrap().state is not PrinterState.UNREACHABLE


@printer_required
def test_an_address_with_nothing_at_it_comes_back_as_a_message_not_a_hang(tmp_path):
    """A timeout has to be a sentence, not a frozen window."""
    payload = tmp_path / "x.gcode.3mf"
    payload.write_bytes(b"x")

    outcome = BambuLanGateway(timeout=5.0).send(
        PrintJob(
            file_path=payload,
            connection=PrinterConnection(
                host="192.0.2.1",  # TEST-NET-1: reserved, never routed
                serial=configured().serial,
                access_code=configured().access_code,
            ),
        )
    )

    assert not outcome.ok
    assert "could not be reached" in outcome.error
