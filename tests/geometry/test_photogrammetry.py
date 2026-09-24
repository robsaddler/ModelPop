"""The reconstruction adapter, without running a reconstruction.

Seven subprocesses across two external programs cannot be unit tested, and the
real run is an integration test that skips without the tools. What *can* be
tested here is everything the adapter decides on its own - and that is where its
two nastiest failure modes live, both found by running the tools by hand first
(`docs/research/spike-photogrammetry.md`):

COLMAP's mapper writes to a *numbered* subdirectory, and writes none at all
when reconstruction failed, while still exiting zero. And OpenMVS says nothing
on stdout, so a failure can only be explained by reading the log it left behind.
"""

import pytest

from modelpop.application.reconstruction_ports import (
    Quality,
    Reconstruction,
    Stage,
    progress_through,
)
from modelpop.domain.mesh import Mesh
from modelpop.vision.photogrammetry import (
    COLMAP_VARIABLE,
    OPENMVS_VARIABLE,
    ColmapOpenMvsReconstructor,
    find_colmap,
    find_openmvs,
    newest_log,
    pick_model_directory,
)

from .strategies import unit_cube


class TestChoosingTheModel:
    """The failure with no error attached: a mapper that solved nothing."""

    def model_at(self, root, name: str, size: int = 8):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "images.bin").write_bytes(b"\x00" * size)
        return folder

    def test_the_one_model_is_chosen(self, tmp_path):
        made = self.model_at(tmp_path, "0")
        assert pick_model_directory(tmp_path) == made

    def test_no_model_at_all_is_the_failure_that_matters(self, tmp_path):
        """COLMAP exits zero having written nothing when it cannot solve."""
        tmp_path.joinpath("sparse").mkdir()
        assert pick_model_directory(tmp_path / "sparse") is None

    def test_a_missing_directory_is_not_a_crash(self, tmp_path):
        assert pick_model_directory(tmp_path / "never-made") is None

    def test_a_directory_without_the_listing_is_not_a_model(self, tmp_path):
        (tmp_path / "0").mkdir()
        assert pick_model_directory(tmp_path) is None

    def test_the_biggest_of_several_models_wins(self, tmp_path):
        """Several means the capture came apart; the largest piece is the subject."""
        self.model_at(tmp_path, "0", size=8)
        biggest = self.model_at(tmp_path, "1", size=800)
        self.model_at(tmp_path, "2", size=80)

        assert pick_model_directory(tmp_path) == biggest


class TestReadingTheLog:
    """OpenMVS's only way of saying what went wrong."""

    def test_the_tail_of_the_newest_log_comes_back(self, tmp_path):
        (tmp_path / "old.log").write_text("something ancient", encoding="utf-8")
        newer = tmp_path / "new.log"
        newer.write_text("line one\nline two\nthe actual problem", encoding="utf-8")
        import os
        import time

        os.utime(newer, (time.time() + 10, time.time() + 10))

        assert "the actual problem" in newest_log(tmp_path)

    def test_the_memory_dump_it_always_ends_with_is_left_out(self, tmp_path):
        """Every run ends with pages of INFO that say nothing about the failure."""
        (tmp_path / "a.log").write_text(
            "Error: the scene has no views\n" + "\n".join(f"INFO junk {i}" for i in range(40)),
            encoding="utf-8",
        )
        assert "no views" in newest_log(tmp_path)

    def test_no_log_at_all_admits_it_rather_than_returning_nothing(self, tmp_path):
        """A failure with no explanation beats one that pretends to have none."""
        assert "no log" in newest_log(tmp_path)

    def test_a_directory_that_is_not_there_does_not_raise(self, tmp_path):
        assert newest_log(tmp_path / "gone")


class TestFindingTheTools:
    def test_an_explicit_setting_wins(self, tmp_path, monkeypatch):
        stated = tmp_path / "my-colmap.exe"
        stated.write_bytes(b"x")
        monkeypatch.setenv(COLMAP_VARIABLE, str(stated))

        assert find_colmap() == stated

    def test_an_explicit_setting_pointing_nowhere_finds_nothing(self, tmp_path, monkeypatch):
        """Better than silently falling back to a different install."""
        monkeypatch.setenv(COLMAP_VARIABLE, str(tmp_path / "absent.exe"))
        assert find_colmap() is None

    def test_openmvs_is_found_by_its_tools_not_its_folder_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv(OPENMVS_VARIABLE, str(tmp_path))
        assert find_openmvs() == tmp_path


class TestSayingWhatIsMissing:
    def io(self):
        class Nothing:
            def load(self, path):
                raise AssertionError("not reached")

            def save(self, mesh, path):
                raise AssertionError("not reached")

            def supported_suffixes(self):
                return frozenset()

        return Nothing()

    def test_neither_tool_present_names_both(self):
        told = ColmapOpenMvsReconstructor(self.io(), colmap=None, openmvs=None).describe()
        assert "COLMAP" in told
        assert "OpenMVS" in told

    def test_a_half_extracted_openmvs_names_what_is_absent(self, tmp_path):
        """The download extracts into a subfolder, which is the usual mistake."""
        told = ColmapOpenMvsReconstructor(
            self.io(), colmap=tmp_path / "colmap.exe", openmvs=tmp_path
        ).describe()

        assert "DensifyPointCloud" in told
        assert "subfolder" in told

    def test_it_is_unavailable_when_anything_is_missing(self, tmp_path):
        assert not ColmapOpenMvsReconstructor(self.io(), colmap=None, openmvs=None).is_available()

    def test_it_refuses_a_bad_photo_set_before_looking_for_tools(self, tmp_path):
        from modelpop.domain.photo_set import PhotoSet

        outcome = ColmapOpenMvsReconstructor(self.io(), colmap=None, openmvs=None).reconstruct(
            PhotoSet.of([tmp_path / "one.jpg"])
        )

        assert not outcome.ok
        assert "not enough" in outcome.error


class TestProgressWeighting:
    """Measured shares, not an even split.

    Densification alone is two thirds of a run. An evenly-spaced bar appears to
    hang for a minute in the middle, which is when people kill a job that is
    working.
    """

    def test_the_stages_weights_sum_to_one_whole_run(self):
        assert sum(stage.weight for stage in Stage) == pytest.approx(1.0)

    def test_progress_only_ever_moves_forward(self):
        seen = [progress_through(stage) for stage in Stage]
        assert seen == sorted(seen)

    def test_it_starts_at_nothing_and_ends_short_of_everything(self):
        assert progress_through(Stage.FEATURES) == pytest.approx(0.0)
        assert 0.0 < progress_through(Stage.MESHING) < 1.0

    def test_the_two_slow_stages_are_most_of_the_bar(self):
        slow = Stage.DENSIFYING.weight + Stage.MESHING.weight
        assert slow > 0.85, "the five quick stages should barely move it"

    def test_part_way_through_a_stage_lands_inside_that_stage(self):
        start = progress_through(Stage.DENSIFYING)
        middle = progress_through(Stage.DENSIFYING, 0.5)
        end = progress_through(Stage.MESHING)

        assert start < middle < end

    def test_a_nonsense_fraction_is_clamped_rather_than_escaping_the_bar(self):
        assert progress_through(Stage.MESHING, 50.0) <= 1.0
        assert progress_through(Stage.FEATURES, -3.0) >= 0.0

    def test_every_stage_says_something_a_person_would_understand(self):
        for stage in Stage:
            assert stage.describe
            assert stage.describe[0].isupper()
            assert "_" not in stage.describe


class TestReportingTheRun:
    def test_a_good_run_reads_plainly(self):
        told = Reconstruction(unit_cube(10), photos_given=24, photos_used=24).describe()
        assert "24 of 24" in told

    def test_a_run_that_placed_few_photographs_says_so(self):
        """The one number that says whether a capture was good enough."""
        thin = Reconstruction(unit_cube(10), photos_given=40, photos_used=6)

        assert thin.is_thin
        assert "15%" in thin.describe()
        assert "overlap" in thin.describe()

    def test_a_run_that_placed_most_of_them_is_not_called_thin(self):
        assert not Reconstruction(unit_cube(10), photos_given=24, photos_used=22).is_thin

    def test_discarding_a_lot_of_background_is_mentioned(self):
        told = Reconstruction(
            unit_cube(10), photos_given=24, photos_used=24, discarded_fraction=0.4
        ).describe()
        assert "40%" in told
        assert "background" in told

    def test_discarding_almost_nothing_is_not_worth_saying(self):
        told = Reconstruction(
            unit_cube(10), photos_given=24, photos_used=24, discarded_fraction=0.01
        ).describe()
        assert "background" not in told

    def test_coverage_of_nothing_does_not_divide_by_zero(self):
        assert Reconstruction(unit_cube(10)).coverage == 0.0
        assert not Reconstruction(unit_cube(10)).is_thin

    def test_provenance_records_that_it_was_measured_not_invented(self):
        told = Reconstruction(
            unit_cube(10), photos_given=24, photos_used=24, seconds=90.0
        ).provenance
        assert "Reconstructed from 24 of 24" in told
        assert "1.5 minutes" in told


class TestQuality:
    def test_every_setting_explains_its_trade(self):
        for quality in Quality:
            assert len(quality.describe) > 20

    def test_an_empty_mesh_still_describes_without_raising(self):
        assert Reconstruction(Mesh.empty()).describe()
