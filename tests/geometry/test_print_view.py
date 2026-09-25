"""Drawing a print in progress.

These build VTK geometry but never rasterise it, so they run anywhere - unlike
the one viewport test that actually draws pixels, which is marked ``renders``
because a GPU-less runner dies mid-render rather than failing.
"""

import textwrap

import numpy as np
import pytest

from modelpop.domain.printer import PrinterProfile
from modelpop.printing.simulate import Frame, VirtualPrint
from modelpop.printing.toolpath import Segment, Toolpath
from modelpop.rendering.print_view import (
    DEFAULT_COLOUR,
    FEATURE_COLOURS,
    colour_of,
    nozzle_marker,
    onto_the_plate,
    progress_of,
    to_lines,
)

HEADER = "; total estimated time: 5m 0s\nM83\nG90\n"


def square(z: float, size: float = 20.0, feature: str = "Outer wall") -> str:
    half = size / 2
    return f"""
        ; CHANGE_LAYER
        ; Z_HEIGHT: {z}
        ; FEATURE: {feature}
        G1 X{100 - half} Y{100 - half} F9000
        G1 X{100 + half} Y{100 - half} E1.0
        G1 X{100 + half} Y{100 + half} E1.0
        G1 X{100 - half} Y{100 + half} E1.0
        G1 X{100 - half} Y{100 - half} E1.0
    """


def column(layers: int = 6) -> VirtualPrint:
    body = HEADER + "".join(square(0.2 * n) for n in range(1, layers + 1))
    return VirtualPrint.of(Toolpath.parse(textwrap.dedent(body).strip().splitlines()))


class TestFeatureColours:
    def test_a_known_feature_gets_its_colour(self):
        assert colour_of("Outer wall") == FEATURE_COLOURS["outer wall"]

    def test_matching_ignores_case(self):
        assert colour_of("OUTER WALL") == colour_of("outer wall")

    def test_a_qualified_name_still_matches(self):
        """The slicer says "Internal Bridge infill", not "bridge"."""
        assert colour_of("Internal Bridge infill") == FEATURE_COLOURS["internal bridge"]

    def test_support_interface_is_distinguishable_from_support(self):
        assert colour_of("Support interface") != colour_of("Support")

    def test_an_unknown_feature_is_drawn_rather_than_hidden(self):
        """A feature name we do not recognise is still material on the plate."""
        assert colour_of("Something The Slicer Invented") == DEFAULT_COLOUR

    def test_no_feature_at_all_is_drawn(self):
        assert colour_of("") == DEFAULT_COLOUR

    def test_every_colour_is_a_six_digit_hex(self):
        for name, colour in FEATURE_COLOURS.items():
            assert colour.startswith("#"), name
            assert len(colour) == 7, name
            int(colour[1:], 16)


class TestDrawingTheToolpath:
    def test_every_move_becomes_one_line(self):
        play = column()
        laid = play.extruded_by(play.total_seconds)
        assert to_lines(laid).n_cells == len(laid)

    def test_a_line_has_two_points(self):
        play = column()
        laid = play.extruded_by(play.total_seconds)
        assert to_lines(laid).n_points == len(laid) * 2

    def test_nothing_laid_down_draws_nothing(self):
        assert to_lines(()).n_cells == 0

    def test_each_line_carries_its_own_colour(self):
        """Per-cell rather than a lookup table: features are categories, and
        interpolating between them would invent colours that mean nothing."""
        play = column()
        poly = to_lines(play.extruded_by(play.total_seconds))

        assert "feature" in poly.cell_data
        assert poly.cell_data["feature"].shape == (poly.n_cells, 3)

    def test_the_colours_match_the_features(self):
        body = HEADER + square(0.2, feature="Outer wall") + square(0.4, feature="Support")
        play = VirtualPrint.of(Toolpath.parse(textwrap.dedent(body).strip().splitlines()))
        poly = to_lines(play.extruded_by(play.total_seconds))

        drawn = {tuple(row) for row in poly.cell_data["feature"]}
        assert len(drawn) == 2, "a wall and its support should not be the same colour"

    def test_height_is_carried_so_a_layer_view_can_use_it(self):
        play = column()
        poly = to_lines(play.extruded_by(play.total_seconds))
        heights = poly.cell_data["height"]

        assert heights.min() == pytest.approx(0.2)
        assert heights.max() > heights.min()

    def test_the_geometry_is_in_millimetres_beside_the_build_plate(self):
        """A toolpath drawn in the wrong unit looks like a scale bug by eye."""
        play = column()
        poly = to_lines(play.extruded_by(play.total_seconds))

        assert 80 < poly.bounds[0] < 120
        assert 0 < poly.bounds[5] < 10

    def test_a_large_toolpath_is_built_in_one_pass(self):
        """Appending a hundred thousand times is the difference between a
        scrub that drags and one that does not."""
        import time

        play = column(layers=250)
        laid = play.extruded_by(play.total_seconds)

        started = time.perf_counter()
        poly = to_lines(laid)
        elapsed = time.perf_counter() - started

        assert poly.n_cells == len(laid)
        assert elapsed < 1.0, f"building {len(laid)} lines took {elapsed:.2f}s"


class TestTheNozzle:
    def test_it_sits_above_the_work(self):
        """Pointing down at the layer being laid, not buried in it."""
        play = column()
        frame = play.at(play.total_seconds / 2)
        marker = nozzle_marker(frame, size=3.0)

        assert marker.center[2] > frame.z
        assert marker.n_points > 0

    def test_it_follows_the_head(self):
        play = column()
        early = nozzle_marker(play.at(0.0))
        late = nozzle_marker(play.at(play.total_seconds))
        assert late.center[2] > early.center[2]


class TestTheProgressLine:
    def test_it_counts_layers_from_one_for_a_human(self):
        play = column()
        assert progress_of(play, 0.0).startswith("Layer 1 of")

    def test_it_reports_time_left_rather_than_time_spent(self):
        """What people actually want from a printer."""
        play = column()
        assert "left" in progress_of(play, 0.0)

    def test_nothing_is_left_at_the_end(self):
        play = column()
        assert "0m 00s left" in progress_of(play, play.total_seconds)

    def test_it_names_the_feature_being_printed(self):
        play = column()
        assert "Outer wall" in progress_of(play, play.total_seconds / 2)

    def test_an_empty_print_says_so_rather_than_lying(self):
        assert progress_of(VirtualPrint.of(Toolpath()), 0.0) == "Nothing to play."


class TestDrawingItWhereThePlateIs:
    """G-code speaks the machine's coordinates. The plate is drawn around zero.

    "Watch it print worked but didn't put the dragon in the right place on the
    plate - printed him semi outside one of the printer corners."

    The print was right; the playback was not. Bambu's bed origin is a corner,
    so a centred model runs from 0 to 256, while every viewport here draws the
    plate from -128 to +128 because that is the sane convention for looking at
    a model. Drawn straight, the toolpath lands half a bed out: over one corner
    with two edges hanging off.
    """

    def printer(self) -> PrinterProfile:
        return PrinterProfile.p2s()

    def a_square(self, at: tuple[float, float], size: float = 20.0):
        """Four extrusion moves round a square, in machine coordinates."""
        x, y = at
        half = size / 2
        corners = [
            (x - half, y - half, 0.2),
            (x + half, y - half, 0.2),
            (x + half, y + half, 0.2),
            (x - half, y + half, 0.2),
        ]
        return tuple(
            Segment(
                start=corners[i],
                end=corners[(i + 1) % 4],
                extrudes=True,
                layer=0,
                feature="outer wall",
            )
            for i in range(4)
        )

    def test_a_model_printed_dead_centre_is_drawn_dead_centre(self):
        printer = self.printer()
        middle = printer.bed_centre
        drawn = to_lines(self.a_square(middle), onto_the_plate(printer)).points

        assert abs(float(drawn[:, 0].mean())) < 1e-6
        assert abs(float(drawn[:, 1].mean())) < 1e-6

    def test_without_the_offset_it_lands_a_half_bed_out(self):
        """The bug, stated as a measurement."""
        printer = self.printer()
        drawn = to_lines(self.a_square(printer.bed_centre)).points
        half = printer.build_width.millimetres / 2

        assert float(drawn[:, 0].mean()) == pytest.approx(half)
        assert float(drawn[:, 1].mean()) == pytest.approx(half)

    def test_it_stays_on_the_plate_that_is_drawn(self):
        printer = self.printer()
        half_wide = printer.build_width.millimetres / 2
        half_deep = printer.build_depth.millimetres / 2
        drawn = to_lines(self.a_square(printer.bed_centre), onto_the_plate(printer)).points

        assert (abs(drawn[:, 0]) <= half_wide).all()
        assert (abs(drawn[:, 1]) <= half_deep).all()

    def test_a_model_printed_off_centre_is_drawn_off_centre(self):
        """It has to tell the truth, not merely centre everything.

        The slicer really can put a model somewhere other than the middle, and
        seeing that is the whole reason to watch a print before starting it.
        """
        printer = self.printer()
        centre_x, centre_y = printer.bed_centre
        drawn = to_lines(
            self.a_square((centre_x - 28.0, centre_y - 28.0)), onto_the_plate(printer)
        ).points

        assert float(drawn[:, 0].mean()) == pytest.approx(-28.0)
        assert float(drawn[:, 1].mean()) == pytest.approx(-28.0)

    def test_the_nozzle_moves_with_the_work(self):
        """A head drawn half a bed from its own toolpath is worse than none."""
        printer = self.printer()
        centre_x, centre_y = printer.bed_centre
        frame = Frame(seconds=0.0, position=(centre_x, centre_y, 5.0), layer=0, segment=0)

        here = np.asarray(nozzle_marker(frame, offset=onto_the_plate(printer)).center)
        assert here[0] == pytest.approx(0.0)
        assert here[1] == pytest.approx(0.0)
