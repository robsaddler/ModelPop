"""The controls for the section view.

The panel owns no state of its own - every control reads and writes the tool -
so what is worth testing is the wiring: that the slider spans the model rather
than the build volume, that setting it programmatically does not read back as a
drag, and that a new model re-ranges it. That last one is the bug with no
symptom: the slider looks fine and simply stops reaching the part.
"""

import pytest
from PySide6.QtWidgets import QApplication

from modelpop.domain.mesh import BoundingBox
from modelpop.presentation.sectioning import Axis, SectionTool
from modelpop.ui.section_dialog import SectionDialog

CUBE = BoundingBox(-20.0, -20.0, 0.0, 20.0, 20.0, 40.0)
TALL = BoundingBox(-5.0, -5.0, 0.0, 5.0, 5.0, 150.0)


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


def panel(bounds: BoundingBox | None = CUBE) -> tuple[SectionDialog, SectionTool]:
    tool = SectionTool()
    tool.fits(bounds)
    tool.turn_on()
    return SectionDialog(tool), tool


class TestTheControls:
    def test_it_opens_showing_where_the_cut_is(self, app):
        dialog, _ = panel()
        assert "Cut left to right" in dialog._summary.text()

    def test_the_slider_starts_in_the_middle(self, app):
        dialog, _ = panel()
        assert dialog._slider.value() == pytest.approx(dialog._slider.maximum() / 2, abs=1)

    def test_dragging_the_slider_moves_the_cut(self, app):
        dialog, tool = panel()
        dialog._slider.setValue(dialog._slider.maximum())

        assert tool.offset > 15.0, "the far end of the cube"

    def test_the_slider_spans_the_model_not_the_build_volume(self, app):
        dialog, tool = panel()
        dialog._slider.setValue(0)
        low = tool.offset
        dialog._slider.setValue(dialog._slider.maximum())

        assert low > -21.0, "a 40 mm cube, not a 256 mm bed"
        assert tool.offset < 21.0

    def test_choosing_an_axis_turns_the_cut(self, app):
        dialog, tool = panel(TALL)
        dialog._axis.setCurrentIndex(2)

        assert tool.axis is Axis.Z

    def test_the_other_half_button_flips_it(self, app):
        dialog, tool = panel()
        dialog._flip.click()

        assert tool.flipped
        assert "far half" in dialog._summary.text()

    def test_a_new_model_re_ranges_the_slider(self, app):
        """The bug with no symptom: the slider looks fine and misses the part."""
        dialog, tool = panel(TALL)
        dialog._axis.setCurrentIndex(2)
        dialog._slider.setValue(dialog._slider.maximum())
        assert tool.offset > 140.0

        tool.fits(CUBE)
        dialog.refresh()

        assert tool.offset <= 40.0
        assert dialog._slider.value() <= dialog._slider.maximum()

    def test_refreshing_does_not_read_back_as_a_drag(self, app):
        """Setting the slider must not move the cut it was set from."""
        dialog, tool = panel()
        tool.move_to(11.0)
        dialog.refresh()

        assert tool.offset == pytest.approx(11.0, abs=0.1)

    def test_a_model_too_thin_to_cut_leaves_the_slider_disabled(self, app):
        dialog, _ = panel(BoundingBox(0.0, 0.0, 0.0, 0.2, 40.0, 40.0))
        assert not dialog._slider.isEnabled()

    def test_it_says_that_nothing_is_being_changed(self, app):
        """A view that cut the model would be a booby trap, so it says it does not."""
        dialog, _ = panel()
        said = " ".join(child.text() for child in dialog.findChildren(type(dialog._summary)))
        assert "cuts the view, not the model" in said

    def test_it_emits_a_change_whenever_the_cut_moves(self, app):
        dialog, _ = panel()
        seen: list[int] = []
        dialog.changed.connect(lambda: seen.append(1))

        dialog._slider.setValue(dialog._slider.maximum())
        dialog._flip.click()
        dialog._axis.setCurrentIndex(1)

        assert len(seen) == 3
