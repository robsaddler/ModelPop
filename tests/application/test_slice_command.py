"""What the slicer is told, and what it is not allowed to do.

"Watch it print worked but didn't put the dragon in the right place on the
plate - printed him semi outside one of the printer corners."

ModelPop centres the model itself before writing it out, and then used to hand
the slicer ``--arrange 1``, which invites it to place the model wherever it
likes. Measured against the real Bambu Studio CLI on a 40x30x20 box written
dead centre at (128, 128): with arranging on it printed centred at (100, 100),
twenty-eight millimetres out in both directions; with it off, at (128, 128)
exactly. On a model the size of the dragon that is the difference between the
middle of the plate and hanging over a corner.

``--orient`` is the same argument about a different freedom: which way up a
model prints is a step in the feature tree, it undoes, and it is frequently the
whole point - so the slicer does not get to overrule it either.

No CLI runs here. What is asserted is the argument list, which is where the
decision lives.
"""

from pathlib import Path

import pytest

from modelpop.application.ports import SliceJob, SupportType
from modelpop.domain.printer import PrinterProfile
from modelpop.printing.bambu_slicer import BambuSlicer


def a_job(**changed) -> SliceJob:
    return SliceJob(
        model_path=Path("model.stl"),
        printer=PrinterProfile.p2s(),
        output_dir=Path("out"),
        **changed,
    )


def command_for(job: SliceJob) -> list[str]:
    return BambuSlicer()._build_command(job, Path("machine.json"), Path("process.json"))


def value_after(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


class TestTheModelStaysWhereItWasPut:
    def test_the_slicer_is_told_not_to_arrange(self):
        assert value_after(command_for(a_job()), "--arrange") == "0"

    def test_the_slicer_is_told_not_to_reorient(self):
        assert value_after(command_for(a_job()), "--orient") == "0"

    @pytest.mark.parametrize("flag", ["--arrange", "--orient"])
    def test_it_can_still_be_asked_for(self, flag):
        """Off by default is a default, not a prohibition."""
        job = a_job(auto_arrange=True, auto_orient=True)
        assert value_after(command_for(job), flag) == "1"


class TestTheRestOfTheCommand:
    """A guard on the shape of the call, not a restatement of it."""

    def test_the_profiles_are_one_argument_joined_by_a_semicolon(self):
        """Trap 3: every profile path has spaces in it."""
        command = command_for(a_job())
        assert value_after(command, "--load-settings") == "machine.json;process.json"

    def test_the_model_is_the_last_argument(self):
        assert command_for(a_job())[-1] == "model.stl"

    def test_supports_are_only_mentioned_when_wanted(self):
        assert "--enable-support" not in command_for(a_job())
        assert "--enable-support" in command_for(a_job(supports=SupportType.TREE_AUTO))
