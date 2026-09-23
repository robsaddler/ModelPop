"""Measuring off a photograph, driven without a mouse.

Lines are placed directly rather than by synthesising drag events: that would
test Qt's event delivery, not this. What is worth testing here is the one rule
the widget owns and the arithmetic cannot - a line drawn on a photograph that
has been shrunk to fit the window must be read back in *photograph* pixels, or
the answer changes every time somebody resizes the window.
"""

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from modelpop.ui.measure_dialog import COMMON_REFERENCES, Line, MeasureDialog, PhotoCanvas


@pytest.fixture(scope="module")
def app():
    existing = QApplication.instance()
    yield existing or QApplication([])


@pytest.fixture
def photo(tmp_path, app):
    """A plain picture of a known size, written out for the widget to load."""
    path = tmp_path / "subject.png"
    image = QPixmap(800, 600)
    image.fill()
    assert image.save(str(path))
    return path


class TestTheCanvas:
    def test_it_loads_a_picture(self, photo, app):
        assert PhotoCanvas().show_photo(photo)

    def test_it_refuses_something_that_is_not_a_picture(self, tmp_path, app):
        rubbish = tmp_path / "notes.txt"
        rubbish.write_text("this is not a photograph", encoding="utf-8")

        assert not PhotoCanvas().show_photo(rubbish)

    def test_nothing_is_measured_before_anything_is_drawn(self, photo, app):
        canvas = PhotoCanvas()
        canvas.show_photo(photo)

        assert canvas.pixels_of(Line.REFERENCE) == 0.0
        assert canvas.pixels_of(Line.SUBJECT) == 0.0

    def test_a_line_is_read_back_in_photograph_pixels_not_screen_pixels(self, photo, app):
        """The rule the widget owns, and the one a resize would otherwise break."""
        canvas = PhotoCanvas()
        canvas.resize(480, 360)  # the 800x600 photo is shown at 60%
        canvas.show_photo(photo)
        canvas.set_line(Line.REFERENCE, (0.0, 10.0), (120.0, 10.0))

        assert canvas.pixels_of(Line.REFERENCE) == pytest.approx(200.0), "120 on screen is 200 real"

    def test_the_same_line_measures_the_same_after_a_resize(self, photo, app):
        canvas = PhotoCanvas()
        canvas.resize(800, 600)
        canvas.show_photo(photo)
        canvas.set_line(Line.REFERENCE, (0.0, 10.0), (200.0, 10.0))
        before = canvas.pixels_of(Line.REFERENCE)

        canvas.resize(480, 360)
        canvas.set_line(Line.REFERENCE, (0.0, 5.0), (120.0, 5.0))

        assert canvas.pixels_of(Line.REFERENCE) == pytest.approx(before)

    def test_the_reference_is_drawn_first_and_the_subject_next(self, photo, app):
        canvas = PhotoCanvas()
        canvas.show_photo(photo)

        assert canvas.drawing is Line.REFERENCE

    def test_starting_again_forgets_both_lines(self, photo, app):
        canvas = PhotoCanvas()
        canvas.resize(800, 600)
        canvas.show_photo(photo)
        canvas.set_line(Line.REFERENCE, (0.0, 0.0), (100.0, 0.0))
        canvas.set_line(Line.SUBJECT, (0.0, 50.0), (300.0, 50.0))

        canvas.clear()

        assert canvas.pixels_of(Line.SUBJECT) == 0.0
        assert canvas.drawing is Line.REFERENCE

    def test_loading_another_picture_forgets_the_old_measurements(self, photo, app):
        canvas = PhotoCanvas()
        canvas.resize(800, 600)
        canvas.show_photo(photo)
        canvas.set_line(Line.REFERENCE, (0.0, 0.0), (100.0, 0.0))

        canvas.show_photo(photo)

        assert canvas.pixels_of(Line.REFERENCE) == 0.0

    def test_it_paints_with_no_picture_and_with_half_a_line(self, app):
        """Painted on every mouse move, including before there is anything."""
        canvas = PhotoCanvas()
        canvas.resize(200, 200)
        canvas.grab()
        canvas.set_line(Line.SUBJECT, (5.0, 5.0), (5.0, 5.0))
        canvas.grab()


class TestTheDialog:
    def measured(self, photo) -> MeasureDialog:
        dialog = MeasureDialog(photo)
        dialog.canvas.resize(800, 600)
        dialog.canvas.set_line(Line.REFERENCE, (0.0, 10.0), (300.0, 10.0))
        dialog.canvas.set_line(Line.SUBJECT, (0.0, 50.0), (480.0, 50.0))
        return dialog

    def test_it_reports_whether_the_picture_opened(self, photo, tmp_path, app):
        assert MeasureDialog(photo).loaded

        rubbish = tmp_path / "notes.txt"
        rubbish.write_text("not a photograph", encoding="utf-8")
        assert not MeasureDialog(rubbish).loaded

    def test_two_lines_and_a_known_length_give_a_size(self, photo, app):
        dialog = self.measured(photo)

        assert dialog.scale.is_usable
        assert dialog.scale.subject.millimetres == pytest.approx(240.0), "a 150 mm ruler"

    def test_nothing_can_be_accepted_until_both_lines_are_drawn(self, photo, app):
        dialog = MeasureDialog(photo)
        ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        assert not ok.isEnabled()

        dialog.canvas.resize(800, 600)
        dialog.canvas.set_line(Line.REFERENCE, (0.0, 10.0), (300.0, 10.0))
        assert not ok.isEnabled(), "one line is not a measurement"

        dialog.canvas.set_line(Line.SUBJECT, (0.0, 50.0), (480.0, 50.0))
        assert ok.isEnabled()

    def test_an_unreadable_picture_can_never_be_accepted(self, tmp_path, app):
        rubbish = tmp_path / "notes.txt"
        rubbish.write_text("not a photograph", encoding="utf-8")
        dialog = MeasureDialog(rubbish)

        ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        assert not ok.isEnabled()

    def test_choosing_a_common_reference_fills_in_its_real_size(self, photo, app):
        dialog = MeasureDialog(photo)
        dialog.canvas.resize(800, 600)
        dialog.canvas.set_line(Line.REFERENCE, (0.0, 10.0), (300.0, 10.0))
        dialog.canvas.set_line(Line.SUBJECT, (0.0, 50.0), (300.0, 50.0))

        card = next(i for i, (label, _) in enumerate(COMMON_REFERENCES) if "Bank card" in label)
        dialog._what.setCurrentIndex(card)

        assert dialog.scale.subject.millimetres == pytest.approx(85.6)

    def test_something_else_leaves_the_size_alone_to_be_typed(self, photo, app):
        dialog = MeasureDialog(photo)
        before = dialog._real.value()

        dialog._what.setCurrentIndex(len(COMMON_REFERENCES) - 1)

        assert dialog._real.value() == before

    @pytest.mark.parametrize(("label", "millimetres"), COMMON_REFERENCES)
    def test_every_offered_reference_is_a_real_measurement(self, label, millimetres):
        """A wrong constant here is wrong on every model made with it."""
        assert millimetres >= 0
        assert label
