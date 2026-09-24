"""What counts as a set of photographs worth reconstructing.

Arithmetic over file names, so all of it runs in microseconds with nothing on
disk. The distinction worth holding on to is between refusing and warning: a
rule that blocks a capture it merely disapproves of is one people route around,
and the user is the one who was in the room.
"""

from pathlib import Path

import pytest

from modelpop.domain.photo_set import (
    MAX_PHOTOS,
    MIN_PHOTOS,
    SUGGESTED_PHOTOS,
    PhotoSet,
)


@pytest.fixture
def shoot(tmp_path):
    """A real folder of real files.

    Real, because `problem` checks the photographs are still there - a set
    whose files have been moved since they were chosen cannot reconstruct, and
    that is worth catching before a subprocess does.
    """

    def make(count: int, suffix: str = ".jpg") -> PhotoSet:
        folder = tmp_path / "shoot"
        folder.mkdir(exist_ok=True)
        for index in range(count):
            (folder / f"img_{index:03d}{suffix}").write_bytes(b"x")
        return PhotoSet.of(folder.glob("*"))

    return make


class TestGatheringThem:
    def test_it_keeps_only_what_the_tools_can_read(self):
        """A folder of photographs routinely has a stray text file in it."""
        mixed = PhotoSet.of([Path("a.jpg"), Path("notes.txt"), Path("b.png")])
        assert len(mixed) == 2

    def test_it_sorts_by_name_because_that_is_capture_order(self):
        shuffled = PhotoSet.of([Path("img_003.jpg"), Path("img_001.jpg"), Path("img_002.jpg")])
        assert [p.name for p in shuffled.photos] == ["img_001.jpg", "img_002.jpg", "img_003.jpg"]

    def test_the_same_photograph_twice_is_once(self):
        assert len(PhotoSet.of([Path("a.jpg"), Path("a.jpg")])) == 1

    def test_capital_extensions_are_read_too(self):
        """Cameras write .JPG as often as .jpg."""
        assert len(PhotoSet.of([Path("A.JPG"), Path("B.PNG")])) == 2

    def test_something_that_is_not_a_list_of_paths_gives_an_empty_set(self):
        """This runs off a file dialog, and the empty answer is handled everywhere."""
        assert len(PhotoSet.of(None)) == 0

    def test_nothing_at_all_is_an_empty_set(self):
        assert len(PhotoSet.of([])) == 0


class TestWhatIsRefused:
    def test_no_photographs_is_refused(self):
        assert "No photographs" in (PhotoSet().problem or "")

    def test_too_few_to_place_a_camera_is_refused_with_the_number(self, shoot):
        """Two views leave an ambiguity a third resolves. Below that, nothing."""
        told = shoot(MIN_PHOTOS - 1).problem or ""
        assert str(MIN_PHOTOS) in told
        assert str(SUGGESTED_PHOTOS) in told

    def test_the_fewest_that_could_work_is_allowed(self, shoot):
        assert shoot(MIN_PHOTOS).is_usable

    def test_more_than_it_will_attempt_is_refused_and_says_why(self, shoot):
        """Every pair is matched against every other, so the work squares."""
        told = shoot(MAX_PHOTOS + 1).problem or ""
        assert "square" in told
        assert str(MAX_PHOTOS) in told

    def test_photographs_that_have_since_been_deleted_are_refused_by_name(self, tmp_path):
        real = tmp_path / "a.jpg"
        real.write_bytes(b"x")
        gone = tmp_path / "b.jpg"
        told = PhotoSet.of([real, gone, tmp_path / "c.jpg"]).problem or ""

        assert "no longer there" in told
        assert "b.jpg" in told

    def test_a_set_that_is_all_present_has_no_complaint(self, tmp_path):
        for index in range(MIN_PHOTOS):
            (tmp_path / f"{index}.jpg").write_bytes(b"x")
        assert PhotoSet.of(tmp_path.glob("*.jpg")).is_usable


class TestWhatIsMerelyAdvised:
    def test_a_thin_set_is_warned_about_rather_than_blocked(self, shoot):
        thin = shoot(SUGGESTED_PHOTOS - 5)

        assert thin.is_usable, "it must not refuse a capture it only disapproves of"
        assert any("thinly" in line for line in thin.advice)

    def test_a_generous_set_needs_no_advice_about_its_size(self, shoot):
        assert not any("thinly" in line for line in shoot(SUGGESTED_PHOTOS + 5).advice)

    def test_a_mix_of_formats_is_pointed_out(self, tmp_path):
        """Usually means two cameras, and the solver assumes one lens."""
        mixed = PhotoSet.of([Path(f"a{i}.jpg") for i in range(10)] + [Path("b.png")])
        assert any("more than one camera" in line for line in mixed.advice)

    def test_one_format_throughout_prompts_nothing(self, shoot):
        assert not any("camera" in line for line in shoot(SUGGESTED_PHOTOS + 1).advice)

    def test_a_refused_set_is_not_also_lectured(self, shoot):
        """Advice on a set that cannot run at all is noise."""
        assert shoot(1).advice == () or shoot(1).problem is not None


class TestWhatTheUserIsTold:
    def test_it_says_how_many_and_from_where(self, shoot):
        told = shoot(24).describe()
        assert "24 photographs" in told
        assert "shoot" in told

    def test_an_empty_set_says_so(self):
        assert "No photographs" in PhotoSet().describe()

    def test_counting_is_the_number_of_usable_photographs(self, shoot):
        assert len(shoot(7)) == 7

    @pytest.mark.parametrize("count", [MIN_PHOTOS, SUGGESTED_PHOTOS, MAX_PHOTOS])
    def test_every_allowed_size_is_usable(self, shoot, count):
        assert shoot(count).is_usable
