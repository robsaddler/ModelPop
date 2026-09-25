"""Which printer the application thinks it is working with, and how it knows.

"The printer name and dimensions in the top left should come from the printer,
right? And only remember the last settings if the printer is offline?"

Mostly yes, with one thing that cannot work that way: **a printer does not
report how big its bed is.** There is no such field in anything it sends. The
size comes from knowing which model it is, and the model comes from a code the
printer announces - ``N7`` is a P2S - or from the setting.

The nozzle is the part that genuinely has to come from the machine. It is the
one thing about a printer that changes without anybody telling the software,
and it moves the thinnest printable wall from 0.84 mm to 1.26 on a 0.6 - which
the thin-wall warning and the thickening that answers it are both aimed at.
"""

from datetime import UTC, datetime, timedelta

import pytest

from modelpop.domain.printer import Nozzle, PrinterProfile
from modelpop.domain.units import Length
from modelpop.domain.which_printer import WhichPrinter, nearest_nozzle
from modelpop.printing.bambu_profiles import KnownPrinters
from modelpop.repositories.printer_memory import JsonPrinterMemory


def an_a1_mini() -> PrinterProfile:
    return PrinterProfile(
        model="Bambu Lab A1 mini",
        build_width=Length.mm(180),
        build_depth=Length.mm(180),
        build_height=Length.mm(180),
    )


class TestTheCatalogue:
    """Read from Bambu Studio's own profiles, with a table taken from the same
    files for a machine that has not got them."""

    def test_the_built_in_table_knows_the_whole_range(self):
        assert len(KnownPrinters(None).all()) == 14

    def test_a_printer_is_recognised_by_the_code_it_announces(self):
        found = KnownPrinters(None).recognise("N7")

        assert found is not None
        assert found.model == "Bambu Lab P2S"

    def test_the_code_is_matched_whatever_its_case(self):
        """It arrives over the network, and its case is not a promise."""
        assert KnownPrinters(None).recognise("bl-p001").model == "Bambu Lab X1 Carbon"

    def test_a_code_it_does_not_know_is_not_guessed_at(self):
        assert KnownPrinters(None).recognise("something else") is None
        assert KnownPrinters(None).recognise("") is None

    def test_the_x1_and_p1_are_250_tall_rather_than_256(self):
        """The reason the numbers are read rather than typed from memory.

        Every X1 and P1 is 250 mm tall, not 256. Assumed the other way, a model
        254 mm high passes as printable and fails on the machine.
        """
        for model in ("Bambu Lab P1S", "Bambu Lab X1 Carbon"):
            profile = KnownPrinters(None).named(model)
            assert profile is not None
            assert profile.build_height.millimetres == 250.0, model

    def test_the_mini_really_is_smaller(self):
        assert KnownPrinters(None).named("Bambu Lab A1 mini").build_width.millimetres == 180.0

    def test_a_profile_directory_that_is_nonsense_falls_back(self, tmp_path):
        """Not being able to list printers is not a reason to refuse to start."""
        assert len(KnownPrinters(tmp_path / "nowhere").all()) == 14


class TestReadingTheNozzle:
    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            (0.4, Nozzle.STANDARD),
            (0.39999, Nozzle.STANDARD),
            (0.6, Nozzle.WIDE),
            (0.2, Nozzle.FINE),
        ],
    )
    def test_a_reported_diameter_becomes_a_nozzle(self, reported, expected):
        assert nearest_nozzle(reported) is expected

    @pytest.mark.parametrize("reported", [0.0, -1.0, 1.2, 0.5])
    def test_anything_else_is_not_guessed_at(self, reported):
        """0.5 is exactly between two sizes and is neither of them."""
        assert nearest_nozzle(reported) is None


class TestWhereEachPartCameFrom:
    def test_before_anything_is_known_it_says_so(self):
        assert "not confirmed with the printer" in WhichPrinter().describe()

    def test_a_live_reading_needs_no_footnote(self):
        known = WhichPrinter().told_by_the_printer(an_a1_mini(), Nozzle.WIDE)

        said = known.describe()
        assert "Bambu Lab A1 mini" in said
        assert "180 mm" in said
        assert "0.60 mm nozzle" in said
        assert "last seen" not in said
        assert known.is_live

    def test_an_old_reading_says_when_it_was(self):
        """A figure from three weeks ago shown as current is the one somebody
        would act on."""
        known = WhichPrinter().told_by_the_printer(
            an_a1_mini(), Nozzle.WIDE, datetime.now(UTC) - timedelta(days=3)
        )

        assert known.is_remembered
        assert not known.is_live
        assert "last seen 3 days ago" in known.describe()

    def test_a_code_it_does_not_recognise_leaves_the_model_alone(self):
        """A real answer must not be replaced with a shrug."""
        known = WhichPrinter().chosen_in_settings(an_a1_mini())

        after = known.told_by_the_printer(None, Nozzle.FINE)

        assert after.profile.model == "Bambu Lab A1 mini"
        assert after.profile.nozzle is Nozzle.FINE
        assert not after.recognised

    def test_choosing_a_model_keeps_the_nozzle_the_printer_reported(self):
        """Which model it is and what is screwed into it are two questions."""
        known = WhichPrinter().told_by_the_printer(an_a1_mini(), Nozzle.WIDE)

        after = known.chosen_in_settings(KnownPrinters(None).named("Bambu Lab P2S"))

        assert after.profile.model == "Bambu Lab P2S"
        assert after.profile.build_width.millimetres == 256.0
        assert after.profile.nozzle is Nozzle.WIDE

    def test_the_nozzle_decides_what_counts_as_a_thin_wall(self):
        """The whole reason this is worth reading off the machine."""
        known = WhichPrinter().told_by_the_printer(None, Nozzle.WIDE)

        assert known.profile.nozzle.minimum_wall.millimetres == pytest.approx(1.26)


class TestRememberingItBetweenRuns:
    def test_what_the_printer_said_survives_a_restart(self, tmp_path):
        memory = JsonPrinterMemory(tmp_path)
        known = WhichPrinter().told_by_the_printer(an_a1_mini(), Nozzle.WIDE)
        memory.save(known)

        again = memory.load(WhichPrinter(), KnownPrinters(None))

        assert again.profile.model == "Bambu Lab A1 mini"
        assert again.profile.nozzle is Nozzle.WIDE
        assert again.profile.build_width.millimetres == 180.0

    def test_it_comes_back_marked_as_remembered_rather_than_current(self, tmp_path):
        memory = JsonPrinterMemory(tmp_path)
        memory.save(
            WhichPrinter().told_by_the_printer(
                an_a1_mini(), Nozzle.WIDE, datetime.now(UTC) - timedelta(days=5)
            )
        )

        again = memory.load(WhichPrinter(), KnownPrinters(None))

        assert again.is_remembered
        assert "last seen" in again.describe()

    def test_nothing_remembered_leaves_the_default_standing(self, tmp_path):
        again = JsonPrinterMemory(tmp_path).load(WhichPrinter(), KnownPrinters(None))

        assert again.profile.model == "Bambu Lab P2S"
        assert again.heard_at is None

    def test_a_file_full_of_rubbish_is_not_an_error(self, tmp_path):
        """A half-written file is the state before anything was written."""
        (tmp_path / "printer.json").write_text("{not json", encoding="utf-8")

        again = JsonPrinterMemory(tmp_path).load(WhichPrinter(), KnownPrinters(None))

        assert again.profile.model == "Bambu Lab P2S"

    def test_nothing_secret_is_written_down(self, tmp_path):
        """The address and access code belong in the credential store."""
        memory = JsonPrinterMemory(tmp_path)
        memory.save(WhichPrinter().told_by_the_printer(an_a1_mini(), Nozzle.WIDE))

        written = memory.path.read_text(encoding="utf-8")
        assert set(__import__("json").loads(written)) == {"model", "nozzle_mm", "heard_at"}
