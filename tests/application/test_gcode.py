"""Reading the toolpath back.

Mostly driven by synthetic G-code written in the same dialect Bambu Studio
emits, so it runs in milliseconds with no slicer. One integration test proves
the dialect assumption against the real thing.
"""

import textwrap

import pytest

from modelpop.domain.printer import PrinterProfile
from modelpop.domain.readiness import Severity
from modelpop.printing import LayerPreview, parse_layers, verify_gcode


def write(tmp_path, body: str):
    path = tmp_path / "test.gcode"
    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return path


def square(z: float, size: float, at: tuple[float, float] = (100.0, 100.0)) -> str:
    """A closed square of extrusion at one height, in Bambu's dialect."""
    x, y = at
    half = size / 2
    return f"""
        ; CHANGE_LAYER
        ; Z_HEIGHT: {z}
        ; FEATURE: Outer wall
        G1 X{x - half} Y{y - half} F9000
        G1 X{x + half} Y{y - half} E1.0
        G1 X{x + half} Y{y + half} E1.0
        G1 X{x - half} Y{y + half} E1.0
        G1 X{x - half} Y{y - half} E1.0
    """


HEADER = "M83 ; use relative distances for extrusion\nG90\n"


class TestParsing:
    def test_layers_are_split_on_the_layer_marker(self, tmp_path):
        path = write(tmp_path, HEADER + square(0.2, 20) + square(0.4, 20) + square(0.6, 20))
        assert len(parse_layers(path)) == 3

    def test_layer_heights_come_from_the_z_height_comment(self, tmp_path):
        path = write(tmp_path, HEADER + square(0.2, 20) + square(0.45, 20))
        layers = parse_layers(path)
        assert layers[0].z == pytest.approx(0.2)
        assert layers[1].z == pytest.approx(0.45)

    def test_a_travel_z_hop_does_not_create_a_layer(self, tmp_path):
        """Splitting on Z changes produces a spurious layer for every hop."""
        path = write(
            tmp_path,
            HEADER
            + square(0.2, 20)
            + """
            G1 Z0.6 F9000
            G1 X50 Y50 F9000
            G1 Z0.2 F9000
            """
            + square(0.4, 20),
        )
        assert len(parse_layers(path)) == 2

    def test_extrusion_is_read_as_relative(self, tmp_path):
        """Bambu emits M83. Read as absolute, every move looks like extrusion."""
        path = write(
            tmp_path,
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X100 Y100 F9000
            G1 X120 Y100 E1.0
            G1 X140 Y100 E1.0
            """,
        )
        assert len(parse_layers(path)[0].points) > 0

    def test_a_retraction_lays_nothing_down(self, tmp_path):
        path = write(
            tmp_path,
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 E-0.8 F1800
            G1 E0.8 F1800
            """,
        )
        assert parse_layers(path) == []

    def test_a_travel_move_lays_nothing_down(self, tmp_path):
        path = write(
            tmp_path,
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X100 Y100 F9000
            G1 X200 Y200 F9000
            """,
        )
        assert parse_layers(path) == []

    def test_a_long_extrusion_is_sampled_along_its_length(self, tmp_path):
        """Endpoints alone would leave the middle of a wall looking unsupported."""
        path = write(
            tmp_path,
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X50 Y100 F9000
            G1 X150 Y100 E5.0
            """,
        )
        assert len(parse_layers(path)[0].points) > 50

    def test_support_material_is_recognised(self, tmp_path):
        path = write(
            tmp_path,
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            ; FEATURE: Support
            G1 X100 Y100 F9000
            G1 X110 Y100 E1.0
            """,
        )
        assert parse_layers(path)[0].has_support

    def test_absolute_extrusion_is_honoured_when_declared(self, tmp_path):
        path = write(
            tmp_path,
            "M82\nG90\n"
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X100 Y100 F9000
            G1 X120 Y100 E1.0
            G1 X140 Y100 E2.0
            G1 X160 Y100 E2.0
            """,
        )
        # the third move does not advance E, so it is a travel
        assert len(parse_layers(path)[0].points) > 0

    def test_an_unreadable_file_yields_nothing_rather_than_raising(self, tmp_path):
        assert parse_layers(tmp_path / "absent.gcode") == []

    def test_an_empty_file_yields_nothing(self, tmp_path):
        assert parse_layers(write(tmp_path, "")) == []

    def test_a_file_of_nonsense_yields_nothing(self, tmp_path):
        assert parse_layers(write(tmp_path, "this is not gcode\nnor is this")) == []


class TestIslands:
    def test_a_stack_of_identical_layers_floats_nothing(self, tmp_path):
        """A column is held up all the way down."""
        body = HEADER + "".join(square(0.2 * n, 20) for n in range(1, 12))
        result = verify_gcode(write(tmp_path, body))

        assert result.unsupported_layers == 0
        assert result.findings == ()

    def test_material_starting_in_mid_air_is_caught(self, tmp_path):
        """A slab that begins away from everything below it will droop."""
        column = "".join(square(0.2 * n, 20, at=(100, 100)) for n in range(1, 6))
        floating = square(1.2, 30, at=(180, 180))
        result = verify_gcode(write(tmp_path, HEADER + column + floating))

        assert result.unsupported_layers >= 1
        assert any(f.rule == "unsupported-island" for f in result.findings)
        assert result.verdict is Severity.WARNING

    def test_the_finding_names_the_layer_and_the_height(self, tmp_path):
        column = "".join(square(0.2 * n, 20, at=(100, 100)) for n in range(1, 6))
        result = verify_gcode(write(tmp_path, HEADER + column + square(1.2, 30, at=(180, 180))))

        island = next(f for f in result.findings if f.rule == "unsupported-island")
        assert "mid-air" in island.message
        assert "mm" in island.message
        assert island.remedy

    def test_a_layer_slightly_wider_than_the_one_below_is_not_an_island(self, tmp_path):
        """Ordinary overhang is not the same as floating, and crying wolf is costly."""
        body = HEADER + square(0.2, 20) + square(0.4, 21) + square(0.6, 22)
        assert verify_gcode(write(tmp_path, body)).unsupported_layers == 0

    def test_findings_are_capped_so_the_panel_stays_readable(self, tmp_path):
        column = square(0.2, 20, at=(100, 100))
        floats = "".join(square(0.2 * n, 20, at=(60 + n * 12, 200)) for n in range(2, 14))
        result = verify_gcode(write(tmp_path, HEADER + column + floats))

        assert result.unsupported_layers > 5
        assert len(result.findings) <= 6

    def test_the_first_layer_is_never_an_island(self, tmp_path):
        """It rests on the plate; nothing can be floating."""
        assert verify_gcode(write(tmp_path, HEADER + square(0.2, 20))).unsupported_layers == 0


class TestFootprint:
    def test_a_tall_thin_print_is_flagged(self, tmp_path):
        body = HEADER + "".join(square(0.2 * n, 4, at=(100, 100)) for n in range(1, 300))
        result = verify_gcode(write(tmp_path, body))
        assert any(f.rule == "first-layer-adhesion" for f in result.findings)

    def test_a_squat_print_is_not_flagged(self, tmp_path):
        body = HEADER + "".join(square(0.2 * n, 60, at=(100, 100)) for n in range(1, 20))
        result = verify_gcode(write(tmp_path, body))
        assert not any(f.rule == "first-layer-adhesion" for f in result.findings)

    def test_the_first_layer_area_is_measured(self, tmp_path):
        result = verify_gcode(write(tmp_path, HEADER + square(0.2, 40)))
        # a 40 mm square outline rasterised at 1 mm: its perimeter, roughly
        assert result.first_layer_area_mm2 > 100


class TestReporting:
    def test_an_unreadable_file_says_so_rather_than_claiming_success(self, tmp_path):
        result = verify_gcode(tmp_path / "absent.gcode")
        assert not result.was_read
        assert "could not be read" in result.summary()

    def test_a_clean_print_says_nothing_is_wrong(self, tmp_path):
        body = HEADER + "".join(square(0.2 * n, 20) for n in range(1, 6))
        assert "nothing wrong" in verify_gcode(write(tmp_path, body)).summary()

    def test_the_tallest_point_is_reported(self, tmp_path):
        body = HEADER + square(0.2, 20) + square(4.8, 20)
        assert verify_gcode(write(tmp_path, body)).tallest_z == pytest.approx(4.8)

    def test_a_different_printer_is_honoured(self, tmp_path):
        from modelpop.domain import Length

        small = PrinterProfile(
            build_width=Length.mm(120), build_depth=Length.mm(120), build_height=Length.mm(120)
        )
        result = verify_gcode(write(tmp_path, HEADER + square(0.2, 20, at=(60, 60))), small)
        assert result.was_read


class TestLayerPreview:
    def test_it_loads_layers_for_scrubbing(self, tmp_path):
        body = HEADER + "".join(square(0.2 * n, 20) for n in range(1, 6))
        preview = LayerPreview.read(write(tmp_path, body))

        assert preview.count == 5
        assert preview.at(0) is not None
        assert preview.height_of(4).millimetres == pytest.approx(1.0)

    def test_an_index_out_of_range_is_none_rather_than_an_error(self, tmp_path):
        preview = LayerPreview.read(write(tmp_path, HEADER + square(0.2, 20)))
        assert preview.at(99) is None
        assert preview.at(-1) is None
        assert preview.height_of(99).millimetres == 0.0
