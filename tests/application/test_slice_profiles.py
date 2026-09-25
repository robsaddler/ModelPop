"""The profile handed to the slicer, and why it has to be a whole one.

"Slicing failed: one of the plate is empty or has no object fully inside it" -
against a model sitting dead centre on the bed, 238 x 194 mm on a 256 mm plate,
with the readiness panel saying "Ready to print".

**Bambu's profiles are a chain and the CLI does not follow it.** The leaf for a
P2S with a 0.4 nozzle holds barely a dozen settings and has no ``printable_area``
in it at all - the bed is defined on a parent shared across the range. Handed
the leaf, the CLI falls back to a default plate far smaller than the printer,
and then refuses a model that is plainly on the bed for being off a bed nobody
can see.

Measured against the real CLI: the ceiling was exactly 143 mm on either axis,
whatever the other axis and the height were doing. 144 failed. Folded flat, the
same 238 x 194 model slices, and so does 240 x 240.

The bed was not the only thing missing. The start G-code lives on the same
parent, so every slice this application produced went out without bed levelling
or a bed temperature - 1,473 lines of the printer's start sequence, absent.
"""

import json

import pytest

from modelpop.printing.bambu_slicer import _with_parents_folded_in


def a_chain(tmp_path) -> None:
    """A parent holding the bed, and a leaf holding almost nothing - which is
    how Bambu really ships them."""
    (tmp_path / "common.json").write_text(
        json.dumps(
            {
                "printable_area": ["0x0", "256x0", "256x256", "0x256"],
                "printable_height": "256",
                "machine_start_gcode": "G28\nG29",
                "nozzle_diameter": ["0.4"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "leaf.json").write_text(
        json.dumps({"inherits": "common", "printer_model": "Bambu Lab P2S"}),
        encoding="utf-8",
    )


class TestFoldingTheParentsIn:
    def test_the_bed_arrives_from_the_parent(self, tmp_path):
        """The whole bug in one assertion: the leaf has no bed of its own."""
        a_chain(tmp_path)
        leaf = json.loads((tmp_path / "leaf.json").read_text(encoding="utf-8"))
        assert "printable_area" not in leaf

        folded = _with_parents_folded_in(tmp_path / "leaf.json")

        assert folded is not None
        assert folded["printable_area"] == ["0x0", "256x0", "256x256", "0x256"]

    def test_the_start_gcode_arrives_too(self, tmp_path):
        """Bed levelling lives on the same parent, and went missing with it."""
        a_chain(tmp_path)
        folded = _with_parents_folded_in(tmp_path / "leaf.json")

        assert "G29" in folded["machine_start_gcode"]

    def test_the_leaf_still_wins(self, tmp_path):
        a_chain(tmp_path)
        (tmp_path / "leaf.json").write_text(
            json.dumps({"inherits": "common", "printable_height": "300"}), encoding="utf-8"
        )
        folded = _with_parents_folded_in(tmp_path / "leaf.json")

        assert folded["printable_height"] == "300"

    def test_nothing_is_left_pointing_at_a_parent(self, tmp_path):
        """The folded profile stands alone, so nothing goes looking again."""
        a_chain(tmp_path)
        assert "inherits" not in _with_parents_folded_in(tmp_path / "leaf.json")

    def test_a_chain_three_deep_is_followed(self, tmp_path):
        (tmp_path / "grandparent.json").write_text(
            json.dumps({"printable_height": "256"}), encoding="utf-8"
        )
        (tmp_path / "parent.json").write_text(
            json.dumps({"inherits": "grandparent", "printable_area": ["0x0"]}), encoding="utf-8"
        )
        (tmp_path / "child.json").write_text(
            json.dumps({"inherits": "parent", "printer_model": "x"}), encoding="utf-8"
        )

        folded = _with_parents_folded_in(tmp_path / "child.json")

        assert folded["printable_height"] == "256"
        assert folded["printable_area"] == ["0x0"]

    def test_a_profile_that_inherits_itself_does_not_hang(self, tmp_path):
        """A print job failing is one thing; the application not coming back is
        another."""
        (tmp_path / "loop.json").write_text(
            json.dumps({"inherits": "loop", "printable_height": "256"}), encoding="utf-8"
        )

        assert _with_parents_folded_in(tmp_path / "loop.json")["printable_height"] == "256"

    def test_a_parent_that_is_not_there_leaves_what_there_is(self, tmp_path):
        (tmp_path / "orphan.json").write_text(
            json.dumps({"inherits": "gone", "printable_height": "256"}), encoding="utf-8"
        )

        assert _with_parents_folded_in(tmp_path / "orphan.json")["printable_height"] == "256"

    def test_a_file_that_will_not_parse_gives_nothing(self, tmp_path):
        """And the caller then passes the original, no worse off than before."""
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

        assert _with_parents_folded_in(tmp_path / "broken.json") is None

    def test_a_file_that_is_not_there_gives_nothing(self, tmp_path):
        assert _with_parents_folded_in(tmp_path / "missing.json") is None


@pytest.mark.integration
class TestAgainstTheInstalledProfiles:
    """The real ones, which is where this was found."""

    def profiles(self):
        from modelpop.printing.bambu_slicer import find_bambu_studio

        found = find_bambu_studio()
        if found is None:
            pytest.skip("Bambu Studio is not installed")
        return found.parent / "resources" / "profiles" / "BBL" / "machine"

    def test_the_shipped_leaf_really_has_no_bed_in_it(self):
        leaf = self.profiles() / "Bambu Lab P2S 0.4 nozzle.json"
        if not leaf.is_file():
            pytest.skip("the installed Bambu Studio arranges its profiles differently")

        assert "printable_area" not in json.loads(leaf.read_text(encoding="utf-8"))

    def test_folded_flat_it_has_the_whole_bed(self):
        leaf = self.profiles() / "Bambu Lab P2S 0.4 nozzle.json"
        if not leaf.is_file():
            pytest.skip("the installed Bambu Studio arranges its profiles differently")

        folded = _with_parents_folded_in(leaf)

        assert folded["printable_area"] == ["0x0", "256x0", "256x256", "0x256"]
        assert "G29" in folded.get("machine_start_gcode", ""), "bed levelling is still missing"
