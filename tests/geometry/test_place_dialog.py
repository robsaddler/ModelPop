"""The panel that moves a part with buttons instead of a gizmo.

Written because the gizmo was unusable rather than broken. Driven by hand with
synthetic mouse events, the viewport handles translate and rotate exactly as
intended - but the arrows start at the centre of the part, so half of each one
is inside its own geometry, and there is no feedback at all until the mouse
comes up. Somebody trying to move a sphere off the plate has no way to tell a
drag that missed from a drag that did nothing.

So the same operations got buttons, and this is what those buttons must do:
reach the command bus, join the feature tree, and undo like everything else.
There is no second path into the model (ADR-0001), which is what makes a panel
this thin safe.
"""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from modelpop.application.modelling import ModellingSession
from modelpop.presentation.modelling_view_model import ModellingViewModel
from modelpop.ui.place_dialog import PlaceDialog

from ..application.test_modelling import DisplacedCompiler, FakeCompiler


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def panel(with_a_part: bool = True) -> tuple[PlaceDialog, ModellingViewModel]:
    view = ModellingViewModel(ModellingSession(FakeCompiler()))
    if with_a_part:
        view.add_box(20, 20, 20)
    return PlaceDialog(view), view


def steps(view: ModellingViewModel) -> list[str]:
    return [line.label for line in view.state.features]


def press(dialog: PlaceDialog, key: Qt.Key) -> None:
    dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


class TestNudging:
    def test_a_direction_button_puts_a_move_in_the_tree(self, app):
        dialog, view = panel()
        dialog._nudge("Z", 1.0)

        assert len(steps(view)) == 2
        assert "Move" in steps(view)[1]

    def test_the_step_size_is_the_one_chosen(self, app):
        dialog, view = panel()
        dialog._step.setCurrentIndex(0)  # 0.1 mm
        dialog._nudge("X", 1.0)

        assert view.state.document.active_features[-1].parameters["dx"] == pytest.approx(0.1)

    def test_the_default_step_is_visible_at_the_zoom_a_part_is_seen_at(self, app):
        """1 mm nudges look like nothing happened, which reads as broken."""
        dialog, view = panel()
        dialog._nudge("Y", 1.0)

        assert view.state.document.active_features[-1].parameters["dy"] == pytest.approx(5.0)

    def test_each_direction_moves_only_its_own_axis(self, app):
        dialog, view = panel()
        dialog._nudge("Y", -1.0)

        moved = view.state.document.active_features[-1].parameters
        assert moved["dy"] == pytest.approx(-5.0)
        assert moved["dx"] == 0.0
        assert moved["dz"] == 0.0

    def test_a_nudge_undoes_like_any_other_step(self, app):
        """The whole reason this goes through the bus rather than the mesh."""
        dialog, view = panel()
        dialog._nudge("Z", 1.0)
        assert len(steps(view)) == 2

        view.undo()
        assert len(steps(view)) == 1


class TestTurning:
    def test_a_quarter_turn_reaches_the_tree(self, app):
        dialog, view = panel()
        dialog._turn(90.0)

        assert "90" in steps(view)[1]

    def test_it_turns_about_the_axis_chosen(self, app):
        dialog, view = panel()
        dialog._axis.setCurrentIndex(1)  # left to right, X
        dialog._turn(45.0)

        assert view.state.document.active_features[-1].parameters["axis"] == "X"

    def test_turning_by_nothing_adds_no_step(self, app):
        """A zero in the box is not an instruction."""
        dialog, view = panel()
        dialog._turn(0.0)

        assert len(steps(view)) == 1


class TestPlacingIt:
    def test_dropping_a_part_sunk_through_the_plate_lifts_it(self, app):
        """The user's own bug: a shape half welded through the bed."""
        dialog, view = sunk_panel(by=7.0)
        dialog._drop()

        assert len(steps(view)) == 2
        assert view.state.document.active_features[-1].parameters["dz"] == pytest.approx(7.0)

    def test_centring_a_part_off_to_one_side_brings_it_back(self, app):
        dialog, view = sunk_panel(by=0.0, across=12.0)
        dialog._centre()

        assert view.state.document.active_features[-1].parameters["dx"] == pytest.approx(-12.0)

    def test_a_part_already_on_the_bed_says_so_rather_than_adding_a_step(self, app):
        """Nothing to do must look different from nothing happening."""
        dialog, view = panel()
        dialog._drop()

        assert len(steps(view)) == 1, "it added a move of zero millimetres"
        assert dialog._reading.text() == "It is already on the bed."

    def test_a_part_already_centred_says_so_too(self, app):
        dialog, view = panel()
        dialog._centre()

        assert len(steps(view)) == 1
        assert dialog._reading.text() == "It is already centred."

    def test_it_refuses_politely_with_nothing_built(self, app):
        dialog, view = panel(with_a_part=False)
        dialog._drop()

        assert steps(view) == []
        assert "Nothing built" in dialog._reading.text()


class TestTheKeyboard:
    """The keys somebody would try first, doing what they look like they do."""

    def test_the_arrow_keys_move_it_about_the_plate(self, app):
        dialog, view = panel()
        press(dialog, Qt.Key.Key_Right)

        assert view.state.document.active_features[-1].parameters["dx"] == pytest.approx(5.0)

    def test_up_is_away_from_the_viewer_not_upwards(self, app):
        """Matching the button it sits next to, which says Back."""
        dialog, view = panel()
        press(dialog, Qt.Key.Key_Up)

        moved = view.state.document.active_features[-1].parameters
        assert moved["dy"] == pytest.approx(5.0)
        assert moved["dz"] == 0.0

    def test_page_up_and_down_change_the_height(self, app):
        dialog, view = panel()
        press(dialog, Qt.Key.Key_PageUp)

        assert view.state.document.active_features[-1].parameters["dz"] == pytest.approx(5.0)

    def test_a_key_it_does_not_use_is_left_alone(self, app):
        dialog, view = panel()
        press(dialog, Qt.Key.Key_A)

        assert len(steps(view)) == 1

    def test_the_keys_do_nothing_with_nothing_built(self, app):
        dialog, view = panel(with_a_part=False)
        press(dialog, Qt.Key.Key_Right)

        assert steps(view) == []


class TestWhatItSaysAboutWhereThePartIs:
    def test_it_reports_the_position_once_there_is_something_to_report(self, app):
        dialog, _ = panel()
        assert "centre" in dialog._reading.text()

    def test_it_says_when_there_is_nothing_built(self, app):
        dialog, _ = panel(with_a_part=False)
        assert "Nothing built" in dialog._reading.text()

    def test_nothing_is_usable_until_there_is_a_part(self, app):
        dialog, _ = panel(with_a_part=False)
        assert not dialog._drop_button.isEnabled()

    def test_everything_is_usable_once_there_is_one(self, app):
        dialog, _ = panel()
        assert all(button.isEnabled() for button in dialog._buttons)


class TestItSurvivesAWorkerThread:
    """The trap this codebase has already paid for twice.

    The view-model announces from whichever thread did the work, and a rebuild
    runs on one. A panel that registered bound methods would touch widgets from
    there - which does not raise, it just silently stops updating.
    """

    def test_the_panel_registers_signal_emitters_rather_than_methods(self, app):
        _dialog, view = panel()
        listeners = [*view._state_listeners, *view._busy_listeners]

        assert listeners
        for listener in listeners:
            assert "emit" in repr(listener), (
                f"{listener!r} is not a signal emitter, so it will run on "
                "whichever thread announced it"
            )


def sunk_panel(by: float = 0.0, across: float = 0.0) -> tuple[PlaceDialog, ModellingViewModel]:
    """A panel over a part that is *not* sitting neatly on the origin.

    The shared fake compiler always returns a cube already seated and centred,
    which is the one arrangement where both placements correctly do nothing.
    """
    view = ModellingViewModel(ModellingSession(DisplacedCompiler(down=by, across=across)))
    view.add_box(20, 20, 20)
    return PlaceDialog(view), view
