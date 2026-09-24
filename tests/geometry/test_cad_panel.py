"""The CAD panel's feature tree, and what it does with an undone step.

The tree used to list only what the model currently has, so undoing a step made
it vanish outright. Asked about directly: an undone step that could be redone
should still be visible, greyed, rather than leaving an enabled button as the
only clue that anything is waiting.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from modelpop.application.modelling import ModellingSession
from modelpop.presentation.modelling_view_model import ModellingViewModel
from modelpop.ui.cad_panel import _SUPPRESSED_COLOUR, _UNDONE_COLOUR, CadPanel

from ..application.test_modelling import FakeCompiler


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def panel() -> tuple[CadPanel, ModellingViewModel]:
    view = ModellingViewModel(ModellingSession(FakeCompiler()))
    return CadPanel(view), view


def rows(cad: CadPanel) -> list[str]:
    return [cad._tree.item(index).text() for index in range(cad._tree.count())]


class TestTheTree:
    def test_it_lists_the_steps_that_built_the_part(self, app):
        cad, view = panel()
        view.add_box(10, 10, 10)
        view.fillet(1)

        assert len(rows(cad)) == 2
        assert rows(cad)[0].startswith("1. ")


class TestWhatUndoLeavesBehind:
    def build_and_undo(self, app) -> tuple[CadPanel, ModellingViewModel]:
        cad, view = panel()
        view.add_box(10, 10, 10)
        view.fillet(1)
        view.undo()
        return cad, view

    def test_an_undone_step_is_still_on_screen(self, app):
        """It used to disappear completely."""
        cad, _ = self.build_and_undo(app)
        assert len(rows(cad)) == 2, f"the undone step is gone: {rows(cad)}"

    def test_the_undone_step_carries_no_number(self, app):
        """It is not part of the model, so it is not a row of it."""
        cad, _ = self.build_and_undo(app)
        assert rows(cad)[0].startswith("1. ")
        assert not rows(cad)[1].startswith("2. ")

    def test_it_names_what_redo_would_bring_back(self, app):
        cad, _ = self.build_and_undo(app)
        assert "Round" in rows(cad)[1]

    def test_it_is_drawn_dimmer_than_the_real_steps(self, app):
        """A live step takes the theme's own text colour - no brush is set on
        it at all - so what is checked is that the undone one overrides it, and
        overrides it downwards."""
        cad, _ = self.build_and_undo(app)

        assert cad._tree.item(0).foreground().style() == Qt.BrushStyle.NoBrush
        undone = cad._tree.item(1).foreground().color()
        assert undone.name().lower() == _UNDONE_COLOUR.lower()
        assert undone.lightness() < QColor(_SUPPRESSED_COLOUR).lightness(), (
            "an undone step should read as fainter than a suppressed one"
        )

    def test_it_cannot_be_selected(self, app):
        """Nothing the tree does to a feature applies to one that is not there."""
        cad, _ = self.build_and_undo(app)
        assert cad._tree.item(1).flags() == Qt.ItemFlag.NoItemFlags

    def test_it_says_what_it_is_when_pointed_at(self, app):
        cad, _ = self.build_and_undo(app)
        assert "Redo" in cad._tree.item(1).toolTip()

    def test_redoing_turns_it_back_into_a_real_step(self, app):
        cad, view = self.build_and_undo(app)
        view.redo()

        assert len(rows(cad)) == 2
        assert rows(cad)[1].startswith("2. ")

    def test_undoing_everything_still_shows_both_steps_waiting(self, app):
        """The empty tree is where the redo flags used to be dropped entirely."""
        cad, view = self.build_and_undo(app)
        view.undo()

        assert len(rows(cad)) == 2
        assert all(not row[0].isdigit() for row in rows(cad))
        assert cad._redo_button.isEnabled()

    def test_doing_something_else_clears_what_was_waiting(self, app):
        cad, view = self.build_and_undo(app)
        view.chamfer(1)

        assert len(rows(cad)) == 2
        assert rows(cad)[1].startswith("2. ")
        assert not cad._redo_button.isEnabled()


class TestGettingToTheMovePanel:
    """The window owns the panel; the button only asks for it."""

    def test_the_button_asks_the_window_to_open_it(self, app):
        cad, view = panel()
        view.add_box(10, 10, 10)
        asked = []
        cad.place_requested.connect(lambda: asked.append(True))

        cad._place_button.click()
        assert asked == [True]

    def test_it_is_not_offered_with_nothing_built(self, app):
        cad, _ = panel()
        assert not cad._place_button.isEnabled()

    def test_it_is_offered_once_there_is_a_part(self, app):
        cad, view = panel()
        view.add_box(10, 10, 10)
        assert cad._place_button.isEnabled()
