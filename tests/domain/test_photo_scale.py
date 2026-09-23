"""Recovering a real size from a photograph.

Rob's original ask was photos with a ruler in shot, for scale. The arithmetic
is similar triangles and takes a paragraph; what needs testing is the honesty
around it - a measurement that is too short to mean anything, a reference so
small that the error swamps the answer, and the distinction between a size that
was measured and one the app picked.
"""

import pytest

from modelpop.domain.photo_scale import MIN_LINE_PIXELS, PhotoScale, Reference, length_of
from modelpop.domain.units import Length


def scale(reference_pixels: float, real_mm: float, subject_pixels: float) -> PhotoScale:
    return PhotoScale(Reference(reference_pixels, Length.mm(real_mm)), subject_pixels)


class TestMeasuringALine:
    def test_a_horizontal_line_is_its_own_length(self):
        assert length_of((10.0, 40.0), (110.0, 40.0)) == pytest.approx(100.0)

    def test_a_diagonal_line_is_measured_corner_to_corner(self):
        assert length_of((0.0, 0.0), (30.0, 40.0)) == pytest.approx(50.0)

    def test_direction_does_not_matter(self):
        assert length_of((110.0, 40.0), (10.0, 40.0)) == pytest.approx(100.0)

    def test_a_line_that_went_nowhere_is_nothing(self):
        assert length_of((5.0, 5.0), (5.0, 5.0)) == 0.0


class TestWhatTheReferenceEstablishes:
    def test_a_ruler_gives_millimetres_per_pixel(self):
        assert Reference(300.0, Length.mm(150.0)).mm_per_pixel == pytest.approx(0.5)

    def test_a_line_too_short_to_be_deliberate_is_not_usable(self):
        assert not Reference(MIN_LINE_PIXELS - 1, Length.mm(150.0)).is_usable
        assert Reference(MIN_LINE_PIXELS, Length.mm(150.0)).is_usable

    def test_a_reference_of_no_size_is_not_usable(self):
        assert not Reference(300.0, Length.mm(0.0)).is_usable

    def test_an_unusable_reference_gives_no_scale_rather_than_dividing_by_zero(self):
        assert Reference(0.0, Length.mm(150.0)).mm_per_pixel == 0.0


class TestWhatThePhotographSays:
    def test_the_subject_is_the_ratio_of_the_two_lines(self):
        """A 150 mm ruler over 300 pixels, a subject over 480: 240 mm."""
        assert scale(300, 150, 480).subject.millimetres == pytest.approx(240.0)

    def test_a_subject_shorter_than_the_reference_comes_out_smaller(self):
        assert scale(300, 150, 60).subject.millimetres == pytest.approx(30.0)

    def test_both_lines_have_to_be_drawn(self):
        assert not scale(300, 150, 0).is_usable
        assert not scale(0, 150, 480).is_usable
        assert scale(300, 150, 480).is_usable

    def test_a_small_reference_against_a_big_subject_is_called_out(self):
        """A pound coin measuring a chair: the answer moves centimetres per pixel."""
        assert scale(30, 23.43, 900).is_a_stretch
        assert "small next to the subject" in scale(30, 23.43, 900).describe()

    def test_a_sensible_pairing_is_not_called_out(self):
        assert not scale(300, 150, 480).is_a_stretch
        assert "small next to the subject" not in scale(300, 150, 480).describe()

    def test_an_unusable_measurement_is_never_called_a_stretch(self):
        """It has nothing to be a stretch about yet."""
        assert not scale(0, 150, 900).is_a_stretch

    def test_it_says_what_was_measured_rather_than_just_the_answer(self):
        told = scale(300, 150, 480).describe()
        assert "150.00 mm" in told
        assert "300 pixels" in told
        assert "240.00 mm" in told

    def test_before_anything_is_drawn_it_says_what_to_do(self):
        assert "Draw a line" in scale(0, 150, 0).describe()
