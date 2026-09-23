"""Remembering the licensing tick.

Every test points the store at a temporary directory, so nothing here touches
the real application data. The interesting cases are all the ways a small file
can be wrong.
"""

import json

from modelpop.domain.licensing import CLAUSE_VERSION, Acceptance
from modelpop.paths import app_data_dir
from modelpop.repositories.acceptance import JsonAcceptanceStore


class TestRoundTrip:
    def test_an_acceptance_survives_a_restart(self, tmp_path):
        JsonAcceptanceStore(tmp_path).save(Acceptance.now())
        assert JsonAcceptanceStore(tmp_path).load().is_current

    def test_nothing_stored_means_not_accepted(self, tmp_path):
        assert not JsonAcceptanceStore(tmp_path).load().is_current

    def test_the_directory_is_created_if_it_is_missing(self, tmp_path):
        deep = tmp_path / "not" / "there" / "yet"
        assert JsonAcceptanceStore(deep).save(Acceptance.now()).ok
        assert (deep / "licence-acceptance.json").exists()

    def test_the_version_is_kept(self, tmp_path):
        JsonAcceptanceStore(tmp_path).save(Acceptance.now())
        assert JsonAcceptanceStore(tmp_path).load().version == CLAUSE_VERSION


class TestWhenTheFileIsWrong:
    """Being asked to tick again is cheap. Believing a false tick is not."""

    def store(self, tmp_path, contents: str) -> JsonAcceptanceStore:
        (tmp_path / "licence-acceptance.json").write_text(contents, encoding="utf-8")
        return JsonAcceptanceStore(tmp_path)

    def test_a_corrupt_file_reads_as_not_accepted(self, tmp_path):
        assert not self.store(tmp_path, "{not json at all").load().is_current

    def test_an_empty_file_reads_as_not_accepted(self, tmp_path):
        assert not self.store(tmp_path, "").load().is_current

    def test_a_json_list_reads_as_not_accepted(self, tmp_path):
        assert not self.store(tmp_path, "[1, 2, 3]").load().is_current

    def test_a_missing_timestamp_reads_as_not_accepted(self, tmp_path):
        assert not self.store(tmp_path, json.dumps({"version": 1})).load().is_current

    def test_a_nonsense_timestamp_reads_as_not_accepted(self, tmp_path):
        body = json.dumps({"version": 1, "accepted_at": "last Tuesday"})
        assert not self.store(tmp_path, body).load().is_current

    def test_a_version_that_is_not_a_number_reads_as_not_accepted(self, tmp_path):
        body = json.dumps({"version": "one", "accepted_at": "2026-01-01T00:00:00+00:00"})
        assert not self.store(tmp_path, body).load().is_current

    def test_an_older_clause_version_asks_again(self, tmp_path):
        body = json.dumps(
            {"version": CLAUSE_VERSION - 1, "accepted_at": "2026-01-01T00:00:00+00:00"}
        )
        assert not self.store(tmp_path, body).load().is_current

    def test_a_timestamp_without_a_timezone_is_still_read(self, tmp_path):
        """Written by an older build, or edited by hand. Do not lose the tick."""
        body = json.dumps({"version": CLAUSE_VERSION, "accepted_at": "2026-01-01T00:00:00"})
        assert self.store(tmp_path, body).load().is_current


class TestWhenSavingFails:
    def test_an_acceptance_with_no_timestamp_is_refused(self, tmp_path):
        result = JsonAcceptanceStore(tmp_path).save(Acceptance(version=1))
        assert not result.ok

    def test_an_unwritable_location_says_so_rather_than_raising(self, tmp_path):
        """A file where the directory should be. Nothing can be written inside it."""
        blocked = tmp_path / "wall"
        blocked.write_text("not a directory", encoding="utf-8")

        result = JsonAcceptanceStore(blocked).save(Acceptance.now())
        assert not result.ok
        assert "asked again" in result.detail

    def test_a_failed_save_leaves_a_previous_acceptance_intact(self, tmp_path):
        """A half-written file must not read as 'never accepted'."""
        store = JsonAcceptanceStore(tmp_path)
        store.save(Acceptance.now())

        store.save(Acceptance(version=99))  # refused: no timestamp
        assert store.load().is_current

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        JsonAcceptanceStore(tmp_path).save(Acceptance.now())
        assert list(tmp_path.glob("*.tmp")) == []


class TestWhereItGoes:
    def test_the_default_location_is_under_the_users_own_data(self):
        assert "modelpop" in str(app_data_dir()).lower()

    def test_windows_uses_local_app_data(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
        assert app_data_dir(windows=True).parts[-2:] == ("Local", "ModelPop")

    def test_elsewhere_honours_the_xdg_variable(self, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/home/someone/.local/share")
        assert app_data_dir(windows=False).parts[-2:] == ("share", "modelpop")

    def test_with_nothing_set_it_falls_back_to_the_home_directory(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert app_data_dir(windows=False).parts[-3:] == (".local", "share", "modelpop")
