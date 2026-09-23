"""Saving and opening a project.

Most of these are about a file that is wrong. Opening someone's saved work is
exactly where a crash is least forgivable, and the cases that matter - a file
from a newer build, a truncated write, a field someone renamed - cannot be
arranged any other way.
"""

import json

import pytest

from modelpop.application.modelling import ModellingSession
from modelpop.application.project_ports import ProjectStore
from modelpop.domain.cad_commands import CreateBox, EdgeSelector, Face, Fillet, Hollow
from modelpop.domain.commands import Document, Origin
from modelpop.projects.project_file import EXTENSION, MAX_FEATURES, JsonProjectStore

from .test_modelling import FakeCompiler


@pytest.fixture
def store() -> JsonProjectStore:
    return JsonProjectStore(written_by="ModelPop test")


def built() -> Document:
    """A small tree with something of everything in it."""
    session = ModellingSession(FakeCompiler())
    session.apply(CreateBox(40, 40, 60))
    session.apply(Fillet(3, EdgeSelector.VERTICAL), Origin.ASSISTANT)
    session.apply(Hollow(1.6, Face.BOTTOM))
    return session.state.document


class TestTheStoreMatchesThePort:
    def test_it_satisfies_the_port(self):
        assert isinstance(JsonProjectStore(), ProjectStore)


class TestRoundTrip:
    def test_a_tree_survives_being_saved_and_opened(self, store, tmp_path):
        original = built()
        store.save(original, tmp_path / "part")

        reopened = store.load(tmp_path / f"part{EXTENSION}").unwrap()
        assert reopened.document.content_hash == original.content_hash

    def test_the_extension_is_added_when_it_is_missing(self, store, tmp_path):
        written = store.save(built(), tmp_path / "part").unwrap()
        assert written.suffix == EXTENSION

    def test_an_extension_the_user_chose_is_kept(self, store, tmp_path):
        written = store.save(built(), tmp_path / "part.json").unwrap()
        assert written.name == "part.json"

    def test_who_asked_for_each_step_is_kept(self, store, tmp_path):
        """So the tree can still show what the assistant did after a reload."""
        store.save(built(), tmp_path / "part")
        reopened = store.load(tmp_path / f"part{EXTENSION}").unwrap()

        origins = [f.origin for f in reopened.document.features]
        assert Origin.ASSISTANT in origins

    def test_the_file_is_readable_by_a_person(self, store, tmp_path):
        """Worth more here than any saving in size: a user can diff two of them."""
        store.save(built(), tmp_path / "part")
        text = (tmp_path / f"part{EXTENSION}").read_text(encoding="utf-8")

        assert "create-box" in text
        assert '"width": 40.0' in text

    def test_no_geometry_is_written(self, store, tmp_path):
        """The tree is the model. Geometry is rebuilt, which is also how a saved
        model picks up a later build's improvements to an operation."""
        store.save(built(), tmp_path / "part")
        payload = json.loads((tmp_path / f"part{EXTENSION}").read_text(encoding="utf-8"))

        assert set(payload) == {"format", "written_by", "written_at", "name", "features"}

    def test_the_writing_version_is_recorded(self, store, tmp_path):
        """For the message when something will not open."""
        store.save(built(), tmp_path / "part")
        assert store.load(tmp_path / f"part{EXTENSION}").unwrap().written_by == "ModelPop test"

    def test_a_directory_is_created_if_it_is_missing(self, store, tmp_path):
        assert store.save(built(), tmp_path / "deep" / "down" / "part").ok


class TestWhenTheFileIsWrong:
    def write(self, tmp_path, contents: str):
        path = tmp_path / f"broken{EXTENSION}"
        path.write_text(contents, encoding="utf-8")
        return path

    def test_a_missing_file_is_reported_rather_than_raised(self, store, tmp_path):
        result = store.load(tmp_path / "absent.modelpop")
        assert not result.ok
        assert "could not be opened" in result.error

    def test_a_file_that_is_not_json_says_what_it_is_not(self, store, tmp_path):
        result = store.load(self.write(tmp_path, "this is not a project"))
        assert not result.ok
        assert "not a ModelPop project" in result.error

    def test_a_json_list_is_not_a_project(self, store, tmp_path):
        assert not store.load(self.write(tmp_path, "[1, 2, 3]")).ok

    def test_a_file_with_no_format_version_is_refused(self, store, tmp_path):
        body = json.dumps({"features": [{"name": "create-box", "parameters": {}}]})
        result = store.load(self.write(tmp_path, body))

        assert not result.ok
        assert "which format" in result.detail

    def test_a_file_with_no_steps_is_refused(self, store, tmp_path):
        body = json.dumps({"format": 1, "features": []})
        assert not store.load(self.write(tmp_path, body)).ok

    def test_a_runaway_file_is_refused_rather_than_opened(self, store, tmp_path):
        body = json.dumps(
            {
                "format": 1,
                "features": [{"name": "create-box", "parameters": {}}] * (MAX_FEATURES + 1),
            }
        )
        result = store.load(self.write(tmp_path, body))

        assert not result.ok
        assert str(MAX_FEATURES) in result.detail

    def test_a_step_with_no_name_is_dropped(self, store, tmp_path):
        body = json.dumps(
            {
                "format": 1,
                "features": [
                    {"parameters": {"radius": 1}},
                    {"name": "create-box", "parameters": {"width": 1, "depth": 1, "height": 1}},
                ],
            }
        )
        assert len(store.load(self.write(tmp_path, body)).unwrap().document.features) == 1

    def test_missing_optional_fields_are_filled_in(self, store, tmp_path):
        """A file that is slightly wrong should open."""
        body = json.dumps(
            {
                "format": 1,
                "features": [
                    {"name": "create-box", "parameters": {"width": 1, "depth": 1, "height": 1}}
                ],
            }
        )
        feature = store.load(self.write(tmp_path, body)).unwrap().document.features[0]

        assert feature.origin is Origin.USER
        assert feature.created_at is not None

    def test_a_nonsense_origin_becomes_the_user(self, store, tmp_path):
        body = json.dumps(
            {
                "format": 1,
                "features": [{"name": "create-box", "parameters": {}, "origin": "the cat"}],
            }
        )
        feature = store.load(self.write(tmp_path, body)).unwrap().document.features[0]
        assert feature.origin is Origin.USER

    def test_a_nonsense_timestamp_does_not_stop_the_file_opening(self, store, tmp_path):
        """It is cosmetic and excluded from the hash. Guessing beats refusing."""
        body = json.dumps(
            {
                "format": 1,
                "features": [
                    {"name": "create-box", "parameters": {}, "created_at": "last Tuesday"}
                ],
            }
        )
        assert store.load(self.write(tmp_path, body)).ok


class TestAFileFromAnotherVersion:
    def write(self, tmp_path, features: list[dict]) -> object:
        path = tmp_path / f"future{EXTENSION}"
        path.write_text(
            json.dumps({"format": 1, "written_by": "ModelPop 9", "features": features}),
            encoding="utf-8",
        )
        return path

    def test_an_unsupported_step_is_kept_rather_than_dropped(self, store, tmp_path):
        """Dropping it would destroy the user's work the first time they opened
        an old project in a new build and pressed save."""
        path = self.write(
            tmp_path,
            [
                {"name": "create-box", "parameters": {"width": 1, "depth": 1, "height": 1}},
                {"name": "loft", "parameters": {"sections": 3}},
            ],
        )
        saved = store.load(path).unwrap()

        assert len(saved.document.features) == 2
        assert saved.unknown == ("loft",)
        assert not saved.is_complete

    def test_saving_it_again_keeps_the_unsupported_step(self, store, tmp_path):
        path = self.write(
            tmp_path,
            [
                {"name": "create-box", "parameters": {"width": 1, "depth": 1, "height": 1}},
                {"name": "loft", "parameters": {"sections": 3}},
            ],
        )
        reopened = store.load(path).unwrap().document
        store.save(reopened, tmp_path / "again")

        again = store.load(tmp_path / f"again{EXTENSION}").unwrap()
        assert "loft" in again.unknown

    def test_a_file_this_build_fully_understands_says_so(self, store, tmp_path):
        store.save(built(), tmp_path / "part")
        assert store.load(tmp_path / f"part{EXTENSION}").unwrap().is_complete


class TestThroughTheSession:
    def session(self, tmp_path) -> ModellingSession:
        return ModellingSession(FakeCompiler(), JsonProjectStore())

    def test_a_model_can_be_saved_and_reopened(self, tmp_path):
        first = self.session(tmp_path)
        first.apply(CreateBox(40, 40, 60))
        first.apply(Fillet(3))
        assert first.save_to(tmp_path / "part").ok

        second = self.session(tmp_path)
        assert second.open_from(tmp_path / f"part{EXTENSION}").ok
        assert len(second.state.features) == 2

    def test_an_empty_model_is_not_saved(self, tmp_path):
        result = self.session(tmp_path).save_to(tmp_path / "part")
        assert not result.ok
        assert "nothing to save" in result.error

    def test_opening_rebuilds_the_geometry(self, tmp_path):
        """A project that will not build should say so on opening, not later."""
        first = self.session(tmp_path)
        first.apply(CreateBox(40, 40, 60))
        first.save_to(tmp_path / "part")

        second = self.session(tmp_path)
        second.open_from(tmp_path / f"part{EXTENSION}")
        assert second.state.has_geometry

    def test_opening_is_not_an_undoable_step(self, tmp_path):
        first = self.session(tmp_path)
        first.apply(CreateBox(40, 40, 60))
        first.save_to(tmp_path / "part")

        second = self.session(tmp_path)
        second.open_from(tmp_path / f"part{EXTENSION}")
        assert not second.state.can_undo

    def test_a_partly_supported_project_opens_and_says_what_is_missing(self, tmp_path):
        path = tmp_path / f"future{EXTENSION}"
        path.write_text(
            json.dumps(
                {
                    "format": 1,
                    "features": [
                        {"name": "create-box", "parameters": {"width": 1, "depth": 1, "height": 1}},
                        {"name": "loft", "parameters": {}},
                    ],
                }
            ),
            encoding="utf-8",
        )

        session = self.session(tmp_path)
        result = session.open_from(path)

        assert not result.ok, "a partial open must not look like a clean one"
        assert "loft" in result.detail
        assert len(session.state.features) == 2, "but the model is still loaded"

    def test_without_a_store_saving_says_so(self, tmp_path):
        session = ModellingSession(FakeCompiler())
        session.apply(CreateBox(10, 10, 10))
        assert not session.save_to(tmp_path / "part").ok
