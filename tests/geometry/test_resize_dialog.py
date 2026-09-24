"""Scaling an object up and down.

Buttons first, and that is the point of it. Resizing by hand is "a bit
bigger", "half that", "double it" - proportions judged by eye against the rest
of the plate - and making somebody work out a millimetre figure to say that is
the rigidity this application was called out for. The typed size stays, for
when the number really is the point.
"""

import pytest
from PySide6.QtWidgets import QApplication

from modelpop.application.modelling import ModellingSession
from modelpop.domain.units import Length
from modelpop.presentation.modelling_view_model import ModellingViewModel
from modelpop.ui.resize_dialog import ResizeObjectDialog

from ..application.test_modelling import FakeCompiler


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def panel(with_a_part: bool = True) -> tuple[ResizeObjectDialog, ModellingViewModel]:
    view = ModellingViewModel(ModellingSession(FakeCompiler()))
    if with_a_part:
        view.add_box(20, 20, 20)
    return ResizeObjectDialog(view), view


def last_scale(view: ModellingViewModel) -> float:
    step = view.state.document.active_features[-1]
    assert step.name == "scale-to", f"the last step was {step.name}, not a resize"
    return float(step.parameters["height_mm"])


class TestByEye:
    def test_bigger_scales_up_from_the_size_it_is_now(self, app):
        """The fake part is a 10 mm cube, so +10% is 11 mm."""
        _dialog, view = panel()
        view.scale_selected_by(1.1)
        assert last_scale(view) == pytest.approx(11.0)

    def test_smaller_scales_down(self, app):
        _dialog, view = panel()
        view.scale_selected_by(0.5)
        assert last_scale(view) == pytest.approx(5.0)

    def test_the_buttons_are_proportions_not_sizes(self, app):
        """Half of a 10 mm part and half of a 100 mm part are different numbers."""
        _dialog, view = panel()
        view.scale_selected_by(2.0)
        first = last_scale(view)
        view.scale_selected_by(2.0)

        assert last_scale(view) == pytest.approx(first * 2)

    def test_it_lands_in_the_tree_and_undoes(self, app):
        _dialog, view = panel()
        before = len(view.state.features)
        view.scale_selected_by(1.1)
        assert len(view.state.features) == before + 1

        view.undo()
        assert len(view.state.features) == before

    def test_a_wild_factor_is_refused_rather_than_applied(self, app):
        """A slipped decimal point must not put the part outside the room."""
        _dialog, view = panel()
        outcomes: list = []
        view.on_outcome(outcomes.append)
        view.scale_selected_by(5000.0)

        assert outcomes and outcomes[-1].refused
        assert view.state.features[-1].label.startswith("Add")

    def test_it_says_so_when_nothing_is_selected(self, app):
        _dialog, view = panel(with_a_part=False)
        outcomes: list = []
        view.on_outcome(outcomes.append)
        view.scale_selected_by(1.1)

        assert outcomes and outcomes[-1].refused


class TestByNumber:
    def test_a_typed_size_is_taken_as_the_largest_side(self, app):
        _dialog, view = panel()
        view.scale_selected_to(Length.mm(60))
        assert last_scale(view) == pytest.approx(60.0)

    def test_inches_are_understood(self, app):
        dialog, view = panel()
        dialog._typed.setText("2 inches")
        dialog._apply_typed()
        assert last_scale(view) == pytest.approx(50.8, abs=0.01)

    def test_nonsense_is_not_offered_as_a_size(self, app):
        dialog, _view = panel()
        dialog._typed.setText("as big as a house")
        assert not dialog._apply.isEnabled()

    def test_a_size_of_nothing_is_not_offered_either(self, app):
        dialog, _view = panel()
        dialog._typed.setText("0mm")
        assert not dialog._apply.isEnabled()

    def test_a_real_size_is_offered(self, app):
        dialog, _view = panel()
        dialog._typed.setText("45mm")
        assert dialog._apply.isEnabled()


class TestWhatItSays:
    def test_it_reports_the_size_of_the_selected_object(self, app):
        dialog, view = panel()
        assert view.selected_body is not None
        assert view.selected_body.label in dialog._reading.text()
        assert "mm" in dialog._reading.text()

    def test_it_says_when_nothing_is_selected(self, app):
        dialog, _view = panel(with_a_part=False)
        assert "Nothing is selected" in dialog._reading.text()

    def test_nothing_is_usable_without_an_object(self, app):
        dialog, _view = panel(with_a_part=False)
        assert not any(button.isEnabled() for button in dialog._buttons)

    def test_it_follows_the_selection(self, app):
        """Two objects, and the panel must describe whichever is in hand."""
        dialog, view = panel()
        view.add_sphere(5)
        assert view.bodies[1].label in dialog._reading.text()

        view.select(view.bodies[0].id)
        assert view.bodies[0].label in dialog._reading.text()


class TestItSurvivesAWorkerThread:
    def test_the_panel_registers_signal_emitters_rather_than_methods(self, app):
        _dialog, view = panel()
        listeners = [*view._state_listeners, *view._busy_listeners]

        assert listeners
        for listener in listeners:
            assert "emit" in repr(listener), (
                f"{listener!r} is not a signal emitter, so it will run on "
                "whichever thread announced it"
            )
