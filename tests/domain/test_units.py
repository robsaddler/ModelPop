import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from modelpop.domain import Length, Unit

# Lengths that are physically plausible for a 3D printer, avoiding the extremes
# where float comparison stops being meaningful.
lengths = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
positive_lengths = st.floats(min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False)


class TestConversion:
    def test_one_inch_is_25_point_4_mm(self):
        assert Length.inches(1).millimetres == pytest.approx(25.4)

    def test_units_compare_by_physical_size_not_by_unit(self):
        assert Length.inches(1) == Length.mm(25.4)
        assert Length.cm(10) == Length.mm(100)
        assert Length.metres(1) == Length.cm(100)

    def test_six_inches_is_the_dragon_from_the_brief(self):
        # "about 6 inches tall" -> 152.4 mm, comfortably inside the P2S 256 mm envelope
        assert Length.inches(6).to(Unit.MILLIMETRE) == pytest.approx(152.4)

    @given(positive_lengths)
    def test_round_trip_through_any_unit_is_lossless(self, value):
        for unit in Unit:
            assert Length.of(value, unit).to(unit) == pytest.approx(value, rel=1e-12)


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "expected_mm"),
        [
            ("150mm", 150.0),
            ("150 mm", 150.0),
            ("6 inches", 152.4),
            ('6"', 152.4),
            ("2.5cm", 25.0),
            ("1 m", 1000.0),
            ("1ft", 304.8),
            ("42", 42.0),  # bare number means millimetres
            ("  7.5 CM  ", 75.0),
            ("-3mm", -3.0),
            ("1e2mm", 100.0),
        ],
    )
    def test_parses_what_a_human_would_type(self, text, expected_mm):
        assert Length.parse(text).millimetres == pytest.approx(expected_mm)

    @pytest.mark.parametrize("text", ["", "   ", "mm", "abc", "12 furlongs", "--3mm"])
    def test_rejects_nonsense(self, text):
        with pytest.raises(ValueError):
            Length.parse(text)


class TestArithmetic:
    @given(lengths, lengths)
    def test_addition_then_subtraction_is_identity(self, a, b):
        x, y = Length.mm(a), Length.mm(b)
        assert (x + y - y).millimetres == pytest.approx(x.millimetres, abs=1e-6)

    @given(positive_lengths, st.floats(min_value=0.001, max_value=1000))
    def test_scaling_by_s_then_one_over_s_is_identity(self, value, factor):
        original = Length.mm(value)
        assert (original * factor / factor).is_close(original, Length.mm(1e-6))

    def test_dividing_by_zero_is_an_error_not_an_infinity(self):
        with pytest.raises(ZeroDivisionError):
            Length.mm(10) / 0

    def test_ratio_against_zero_is_an_error(self):
        with pytest.raises(ZeroDivisionError):
            Length.mm(10).ratio(Length.mm(0))

    @given(positive_lengths, positive_lengths)
    def test_ratio_is_the_inverse_of_multiplication(self, a, b):
        x, y = Length.mm(a), Length.mm(b)
        assert (y * x.ratio(y)).is_close(x, Length.mm(abs(a) * 1e-9 + 1e-9))

    def test_lengths_order_by_physical_size(self):
        assert Length.mm(1) < Length.cm(1) < Length.inches(1) < Length.metres(1)
        assert max(Length.inches(1), Length.mm(30)) == Length.mm(30)


class TestFormatting:
    def test_formats_in_the_requested_unit(self):
        assert Length.mm(152.4).format(Unit.INCH, places=1) == "6.0 in"
        assert str(Length.inches(1)) == "25.40 mm"

    def test_is_hashable_so_it_can_key_a_cache(self):
        assert len({Length.mm(25.4), Length.inches(1), Length.mm(10)}) == 2


class TestUnitParsing:
    @pytest.mark.parametrize(
        ("text", "unit"),
        [("mm", Unit.MILLIMETRE), ("INCHES", Unit.INCH), (" Cm ", Unit.CENTIMETRE)],
    )
    def test_accepts_aliases_case_insensitively(self, text, unit):
        assert Unit.parse(text) is unit

    def test_every_unit_knows_its_size_in_millimetres(self):
        assert all(u.millimetres > 0 and math.isfinite(u.millimetres) for u in Unit)
