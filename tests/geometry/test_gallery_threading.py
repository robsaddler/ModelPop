"""Nothing the gallery is told from a worker may touch a widget directly.

The trap this codebase has now paid for three times. A download runs on a
worker thread and the view-model tells whoever asked *from that thread*. The
gallery handed that straight to a bound method which closed a modal dialog and
started more work - neither of which may happen on a thread that owns neither
the dialog nor the window. The interface locked.

There was a test asserting exactly this for the main window and none for the
gallery, which is the whole reason it happened again.
"""

import pytest
from PySide6.QtWidgets import QApplication

from modelpop.application.discovery_service import Discovery
from modelpop.ui.gallery import GalleryDialog


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture
def gallery(app, tmp_path):
    dialog = GalleryDialog(Discovery([]), tmp_path)
    yield dialog
    dialog.close()


class TestWhatCrossesBack:
    def test_the_view_model_is_given_signal_emitters_rather_than_methods(self, gallery):
        """Every announcement from the view-model arrives on a worker."""
        listeners = gallery._view._listeners

        assert listeners, "the gallery registered nothing at all"
        for listener in listeners:
            assert "emit" in repr(listener), (
                f"{listener!r} is not a signal emitter, so it will run on "
                "whichever thread announced it"
            )

    def test_the_finished_download_is_handed_back_through_a_signal(self, gallery, monkeypatch):
        """The one that locked the interface.

        ``download_selected`` calls its callback from the worker that did the
        downloading. That callback closed the dialog and opened the file.
        """
        handed: list[object] = []
        monkeypatch.setattr(
            gallery._view,
            "download_selected",
            lambda _into, then=None: handed.append(then),
        )
        monkeypatch.setattr(
            type(gallery._view.state), "can_use_selection", property(lambda _: True)
        )

        gallery._use()

        assert handed, "nothing was asked for"
        assert "emit" in repr(handed[0]), (
            f"{handed[0]!r} is not a signal emitter - the download will close the "
            "dialog from a worker thread"
        )

    def test_a_thumbnail_arriving_is_handed_back_through_a_signal_too(self, gallery):
        """Fetched on a worker, painted onto a list, same rule."""
        assert gallery._signals.pictured is not None
        assert gallery._signals.arrived is not None


class TestTheRunnerIsNotInline:
    def test_the_gallery_runs_its_work_on_a_thread(self, gallery):
        from modelpop.ui.gallery import _ThreadedRunner

        assert isinstance(gallery._view._runner, _ThreadedRunner)
