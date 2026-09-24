"""The list of shapes made this session.

The panel owns no state - it redraws from the history and emits a row number.
What is worth testing is the wiring, and one trap in particular: setting the
selection must not signal, or every redraw puts the shape back in the viewport,
including the redraw caused by having just put it there.
"""

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from modelpop.domain.mesh import Mesh
from modelpop.presentation.variants import GenerationHistory, Variant
from modelpop.ui.variants_panel import VariantsPanel


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def box(size: float = 20.0) -> Mesh:
    half = size / 2
    vertices = np.array(
        [
            [-half, -half, -half],
            [half, -half, -half],
            [half, half, -half],
            [-half, half, -half],
            [-half, -half, half],
            [half, -half, half],
            [half, half, half],
            [-half, half, half],
        ],
        dtype=np.float64,
    )
    faces = np.array(
        [
            [0, 3, 2],
            [0, 2, 1],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [2, 3, 7],
            [2, 7, 6],
            [1, 2, 6],
            [1, 6, 5],
            [0, 4, 7],
            [0, 7, 3],
        ],
        dtype=np.int32,
    )
    return Mesh(vertices, faces)


def history_of(count: int) -> GenerationHistory:
    history = GenerationHistory()
    history.add_all(
        Variant(
            mesh=box(10 + index), provenance="Generated", label=f"Shape {index + 1}", seed=index + 1
        )
        for index in range(count)
    )
    return history


class TestShowingThem:
    def test_it_is_hidden_until_something_has_been_made(self, app):
        """An empty list is clutter on the panel people use most."""
        panel = VariantsPanel()
        panel.show_history(GenerationHistory())

        assert not panel.isVisibleTo(panel.parentWidget() or panel)
        assert panel._listing.count() == 0

    def test_a_row_per_shape(self, app):
        panel = VariantsPanel()
        panel.show_history(history_of(3))

        assert panel._listing.count() == 3

    def test_each_row_carries_the_facts_worth_comparing_on(self, app):
        panel = VariantsPanel()
        panel.show_history(history_of(2))
        said = panel._listing.item(0).text()

        assert "Shape 1" in said
        assert "triangles" in said

    def test_the_seed_is_there_so_a_good_one_can_be_asked_for_again(self, app):
        panel = VariantsPanel()
        panel.show_history(history_of(2))

        assert "seed" in panel._listing.item(0).text()

    def test_the_chosen_one_is_highlighted(self, app):
        history = history_of(3)
        history.choose(2)
        panel = VariantsPanel()
        panel.show_history(history)

        assert panel._listing.currentRow() == 2

    def test_redrawing_with_fewer_shapes_does_not_leave_stale_rows(self, app):
        panel = VariantsPanel()
        panel.show_history(history_of(4))
        panel.show_history(history_of(2))

        assert panel._listing.count() == 2


class TestPickingOne:
    def test_clicking_a_row_asks_for_that_shape(self, app):
        panel = VariantsPanel()
        panel.show_history(history_of(3))
        picked: list[int] = []
        panel.chosen.connect(picked.append)

        panel._listing.setCurrentRow(2)

        assert picked == [2]

    def test_redrawing_does_not_ask_for_anything(self, app):
        """The trap: a redraw that signals puts the shape back in the viewport,
        including the redraw caused by having just put it there."""
        panel = VariantsPanel()
        picked: list[int] = []
        panel.chosen.connect(picked.append)

        panel.show_history(history_of(3))
        panel.show_history(history_of(3))

        assert picked == []

    def test_clearing_the_list_asks_for_nothing(self, app):
        panel = VariantsPanel()
        panel.show_history(history_of(3))
        picked: list[int] = []
        panel.chosen.connect(picked.append)

        panel.show_history(GenerationHistory())

        assert picked == []

    def test_it_says_how_many_are_being_held(self, app):
        panel = VariantsPanel()
        panel.show_history(history_of(3))

        assert "3 shapes" in panel._summary.text()
