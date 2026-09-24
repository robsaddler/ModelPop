"""Finding models an earlier run left behind.

Generating a shape from a picture is about a minute, and the result lands in a
temporary file. Asked for after exactly the case it exists to answer: a model
that could not be worked with, and no way to get it back without spending the
minute again.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from modelpop.presentation.earlier_models import (
    LEAST_INTERESTING_BYTES,
    EarlierModel,
    found_in,
    where_they_land,
)


def leave_one(directory, name: str, size: int = 200_000, ago_seconds: float = 0.0):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if ago_seconds:
        when = time.time() - ago_seconds
        import os

        os.utime(path, (when, when))
    return path


class TestFindingThem:
    def test_it_finds_a_generated_model(self, tmp_path):
        leave_one(tmp_path, "modelpop-generated-1234-generated.glb")
        assert len(found_in(tmp_path)) == 1

    def test_it_finds_a_reconstruction(self, tmp_path):
        leave_one(tmp_path, "modelpop-reconstructed-99-model.ply")
        assert len(found_in(tmp_path)) == 1

    def test_it_finds_meshes_kept_for_the_scene(self, tmp_path):
        leave_one(tmp_path, "modelpop-scene-500/body-1-abc.stl")
        assert len(found_in(tmp_path)) == 1

    def test_it_leaves_other_programs_files_alone(self, tmp_path):
        leave_one(tmp_path, "something-else-entirely.glb")
        assert found_in(tmp_path) == ()

    def test_it_ignores_a_file_that_is_not_a_model(self, tmp_path):
        leave_one(tmp_path, "modelpop-generated-1-run.log")
        assert found_in(tmp_path) == ()

    def test_it_ignores_a_stub_left_by_a_failed_write(self, tmp_path):
        leave_one(tmp_path, "modelpop-generated-1-generated.glb", size=LEAST_INTERESTING_BYTES - 1)
        assert found_in(tmp_path) == ()

    def test_the_newest_is_offered_first(self, tmp_path):
        leave_one(tmp_path, "modelpop-generated-1-old.glb", ago_seconds=7200)
        leave_one(tmp_path, "modelpop-generated-2-new.glb", ago_seconds=60)

        found = found_in(tmp_path)
        assert [m.path.name for m in found] == [
            "modelpop-generated-2-new.glb",
            "modelpop-generated-1-old.glb",
        ]

    def test_a_directory_that_is_not_there_is_not_an_error(self, tmp_path):
        assert found_in(tmp_path / "nowhere") == ()

    def test_an_empty_directory_gives_nothing(self, tmp_path):
        assert found_in(tmp_path) == ()

    def test_the_same_file_is_not_offered_twice(self, tmp_path):
        """The patterns can overlap; the list must not."""
        leave_one(tmp_path, "modelpop-generated-1-generated.glb")
        assert len({m.path for m in found_in(tmp_path)}) == len(found_in(tmp_path))


class TestDescribingThem:
    def one(self, seconds_ago: float, size: int = 4_600_000) -> EarlierModel:
        from pathlib import Path

        return EarlierModel(
            path=Path("modelpop-generated-1-generated.glb"),
            made_at=datetime.now(UTC) - timedelta(seconds=seconds_ago),
            bytes=size,
        )

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(10, "just now"), (600, "10 minutes ago"), (7200, "2 hours ago"), (200_000, "2 days ago")],
    )
    def test_it_says_how_long_ago_in_words(self, seconds, expected):
        """The useful question is "is this the one I just made", and a
        timestamp to the second does not answer it any better."""
        assert self.one(seconds).age == expected

    def test_it_says_the_size_in_the_units_a_person_reads(self):
        assert self.one(10).size == "4.4 MB"

    def test_a_small_one_is_given_in_kilobytes(self):
        assert self.one(10, size=40_000).size.endswith("KB")

    def test_the_line_carries_age_size_and_name(self):
        said = self.one(600).describe()
        assert "10 minutes ago" in said
        assert "MB" in said
        assert "generated.glb" in said


class TestWhereTheyLand:
    def test_it_is_the_systems_temporary_folder(self):
        import tempfile
        from pathlib import Path

        assert where_they_land() == Path(tempfile.gettempdir())
