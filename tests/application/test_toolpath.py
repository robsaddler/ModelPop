"""The one G-code parser, and the virtual printer built on it.

Driven by synthetic files written in the dialect Bambu Studio actually emits, so
they run in milliseconds with no slicer. Integration tests prove the dialect
assumptions against the real thing.
"""

import math
import textwrap

import pytest

from modelpop.printing.simulate import Clock, VirtualPrint, check_sequential_clearance
from modelpop.printing.toolpath import FlushMatrix, MachineLimits, Toolpath

HEADER = "M83 ; use relative distances for extrusion\nG90\n"


def gcode(body: str) -> Toolpath:
    """Parse a snippet of G-code, dedented."""
    return Toolpath.parse(textwrap.dedent(body).strip().splitlines())


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


class TestTheDialect:
    def test_layers_are_split_on_the_layer_marker(self):
        assert len(gcode(HEADER + square(0.2, 20) + square(0.4, 20)).layers) == 2

    def test_a_travel_z_hop_does_not_create_a_layer(self):
        """Splitting on Z changes produces a spurious layer for every hop."""
        path = gcode(
            HEADER
            + square(0.2, 20)
            + "\nG1 Z0.6 F9000\nG1 X50 Y50 F9000\nG1 Z0.2 F9000\n"
            + square(0.4, 20)
        )
        assert len(path.layers) == 2

    def test_extrusion_is_read_as_relative(self):
        """Bambu emits M83. Read as absolute, every move looks like extrusion."""
        path = gcode(
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X100 Y100 F9000
            G1 X120 Y100 E1.0
            """
        )
        assert any(s.extrudes for s in path.segments)

    def test_a_retraction_lays_nothing_down(self):
        path = gcode(HEADER + "\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG1 E-0.8 F1800\nG1 E0.8 F1800\n")
        assert path.layers == ()

    def test_a_travel_move_lays_nothing_down(self):
        path = gcode(
            HEADER + "\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG1 X100 Y100 F9000\nG1 X200 Y200 F9000\n"
        )
        assert path.layers == ()
        assert not any(s.extrudes for s in path.segments)

    def test_absolute_extrusion_is_honoured_when_declared(self):
        path = gcode(
            "M82\nG90\n"
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X100 Y100 F9000
            G1 X120 Y100 E1.0
            G1 X140 Y100 E1.0
            """
        )
        # the second move does not advance E, so it is a travel
        assert sum(1 for s in path.segments if s.extrudes) == 1

    def test_support_material_is_recognised(self):
        path = gcode(
            HEADER + "\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\n; FEATURE: Support\n"
            "G1 X100 Y100 F9000\nG1 X110 Y100 E1.0\n"
        )
        assert path.layers[0].has_support
        assert path.segments[-1].is_support

    def test_a_long_extrusion_is_sampled_along_its_length(self):
        """Endpoints alone leave the middle of a wall looking unsupported."""
        path = gcode(
            HEADER + "\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG1 X50 Y100 F9000\nG1 X150 Y100 E5.0\n"
        )
        assert len(path.layers[0].points) > 50

    def test_the_feedrate_is_converted_from_a_minute_to_a_second(self):
        """The file speaks mm/min; everything downstream works in mm/s."""
        path = gcode(HEADER + "\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG1 X10 Y0 E1 F9000\n")
        assert path.segments[0].feedrate == pytest.approx(150.0)

    def test_an_unreadable_file_yields_nothing_rather_than_raising(self, tmp_path):
        assert Toolpath.read(tmp_path / "absent.gcode").layers == ()

    def test_a_file_of_nonsense_yields_nothing(self):
        assert gcode("this is not gcode\nnor is this").layers == ()


class TestArcs:
    """Bambu's default profile emits no extruding arcs, but arc fitting is a
    setting, and losing material when somebody turns it on would be silent."""

    def test_an_extruding_arc_lays_material_down(self):
        path = gcode(
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X110 Y100 F9000
            G2 X90 Y100 I-10 J0 E2.0
            """
        )
        assert len(path.layers) == 1
        assert len(path.layers[0].points) > 10

    def test_an_arc_stays_on_its_radius(self):
        """A chord-only reading would cut the corner and miss the material."""
        path = gcode(
            HEADER
            + """
            ; CHANGE_LAYER
            ; Z_HEIGHT: 0.2
            G1 X110 Y100 F9000
            G3 X90 Y100 I-10 J0 E2.0
            """
        )
        for x, y in path.layers[0].points:
            assert math.hypot(x - 100, y - 100) == pytest.approx(10.0, abs=0.01)

    def test_the_direction_is_honoured(self):
        """G2 is clockwise and G3 anticlockwise; they sweep opposite halves."""
        body = "\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG1 X110 Y100 F9000\n{} X90 Y100 I-10 J0 E2.0\n"
        clockwise = gcode(HEADER + body.format("G2")).layers[0].points
        anticlockwise = gcode(HEADER + body.format("G3")).layers[0].points

        assert clockwise[:, 1].min() < 99.0, "G2 should sweep below the centre"
        assert anticlockwise[:, 1].max() > 101.0, "G3 should sweep above it"

    def test_a_helix_with_no_xy_target_lays_nothing_down(self):
        """The only arc a default Bambu file contains: the layer-change z-hop."""
        path = gcode(HEADER + "\n; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nG3 Z.6 I1.217 J0 P1 F36000\n")
        assert path.layers == ()


class TestWhatTheFileStatesAboutItself:
    def test_the_machine_limits_are_read(self):
        limits = MachineLimits.read(
            ["M201 X20000 Y20000 Z500 E5000", "M203 X600 Y600 Z20 E30", "M205 X9.00 Y9.00"]
        )
        assert limits.accel_print == 20_000
        assert limits.max_speed_xy == 600
        assert limits.jerk_xy == 9.0

    def test_missing_limits_fall_back_to_a_p2s(self):
        assert MachineLimits.read([]).max_speed_xy == 600.0

    def test_the_flush_matrix_is_read(self):
        matrix = FlushMatrix.read(
            ["; flush_volumes_matrix = 0,280,280,280,280,0,280,280,280,280,0,280,280,280,280,0"]
        )
        assert matrix.slots == 4
        assert matrix.between(0, 1) == 280.0

    def test_changing_to_the_same_slot_costs_nothing(self):
        matrix = FlushMatrix.read(["; flush_volumes_matrix = 0,280,280,0"])
        assert matrix.between(1, 1) == 0.0

    def test_a_slot_outside_the_matrix_costs_nothing_rather_than_raising(self):
        matrix = FlushMatrix.read(["; flush_volumes_matrix = 0,280,280,0"])
        assert matrix.between(0, 9) == 0.0

    def test_a_matrix_that_is_not_square_is_ignored(self):
        assert FlushMatrix.read(["; flush_volumes_matrix = 0,280,280"]).slots == 0

    def test_a_file_with_no_matrix_says_nothing_rather_than_guessing(self):
        """An invented purge volume would look measured. It would not be."""
        assert FlushMatrix.read([]).between(0, 1) == 0.0

    def test_the_slicers_own_estimate_is_read_from_the_header(self):
        path = gcode("; model printing time: 12m 25s; total estimated time: 12m 26s\n" + HEADER)
        assert path.stated_seconds == pytest.approx(746.0)

    def test_an_estimate_in_hours_is_read(self):
        path = gcode("; total estimated time: 2h 5m 30s\n" + HEADER)
        assert path.stated_seconds == pytest.approx(7530.0)

    def test_the_progress_ladder_is_read(self):
        path = gcode(HEADER + "\nM73 P0 R12\nM73 P50 R6\nM73 P100 R0\n")
        assert path.progress == ((0.0, 720.0), (0.5, 360.0), (1.0, 0.0))

    def test_tool_changes_are_recorded_in_order(self):
        path = gcode(HEADER + "\nT1\nT3\nT1\nT1\n")
        assert path.tool_changes == ((0, 1), (1, 3), (3, 1))

    def test_filament_weight_is_computed_rather_than_read(self):
        """Bambu's P2S profile reports filament_density = 0, so its own weight
        line reads 0.00 for every print. Believing that field is the bug."""
        path = gcode(
            "; total filament length [mm] : 2487.06\n; filament_diameter = 1.75\n" + HEADER
        )
        assert path.filament_mm3 == pytest.approx(5982.0, rel=0.01)
        assert path.grams() == pytest.approx(7.42, rel=0.01)

    def test_no_filament_stated_weighs_nothing(self):
        assert gcode(HEADER).grams() == 0.0


class TestPlayback:
    def column(self, layers: int = 10) -> VirtualPrint:
        body = "; total estimated time: 5m 0s\n" + HEADER
        body += "".join(square(0.2 * n, 20) for n in range(1, layers + 1))
        return VirtualPrint.of(gcode(body))

    def test_the_slicers_clock_is_preferred(self):
        play = self.column()
        assert play.clock is Clock.SLICER
        assert play.total_seconds == pytest.approx(300.0)

    def test_without_a_stated_time_the_motion_is_used_and_it_says_so(self):
        play = VirtualPrint.of(gcode(HEADER + square(0.2, 20) + square(0.4, 20)))
        assert play.clock is Clock.ESTIMATED
        assert play.total_seconds > 0

    def test_an_estimated_time_is_in_a_believable_range(self):
        """Four 20 mm walls at 150 mm/s is under a second, not a minute."""
        play = VirtualPrint.of(gcode(HEADER + square(0.2, 20)))
        assert 0.1 < play.total_seconds < 5.0

    def test_the_head_starts_at_the_beginning_and_ends_at_the_end(self):
        play = self.column()
        assert play.at(0).layer == 0
        assert play.at(play.total_seconds).layer == len(play.toolpath.layers) - 1

    def test_the_head_climbs_as_the_print_goes_on(self):
        play = self.column()
        heights = [play.at_fraction(f).z for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
        assert heights == sorted(heights)
        assert heights[-1] > heights[0]

    def test_scrubbing_past_either_end_is_clamped_rather_than_fatal(self):
        play = self.column()
        assert play.at(-500).seconds == 0.0
        assert play.at(1e9).seconds == pytest.approx(play.total_seconds)

    def test_material_accumulates_as_the_print_runs(self):
        play = self.column()
        early = len(play.extruded_by(play.total_seconds * 0.2))
        late = len(play.extruded_by(play.total_seconds * 0.9))
        assert 0 < early < late

    def test_only_extruding_moves_are_drawn(self):
        """Travel moves are a different, noisier view."""
        play = self.column()
        assert all(s.extrudes for s in play.extruded_by(play.total_seconds))

    def test_a_layer_can_be_jumped_to(self):
        play = self.column()
        moment = play.seconds_for_layer(5)
        assert play.at(moment).layer >= 5

    def test_an_empty_file_plays_nothing_rather_than_raising(self):
        play = VirtualPrint.of(Toolpath())
        assert not play.can_play
        assert play.at(10).layer == -1
        assert "no toolpath" in play.describe()

    def test_the_description_reads_as_a_duration(self):
        assert "5m 00s" in self.column().describe()

    def test_a_long_print_is_described_in_hours(self):
        body = "; total estimated time: 3h 20m 0s\n" + HEADER + square(0.2, 20) + square(0.4, 20)
        assert "3h 20m" in VirtualPrint.of(gcode(body)).describe()


class TestSequentialPrinting:
    """The one collision that is real, as opposed to the one people fear."""

    def stacked(self, heights: list[float]) -> Toolpath:
        body = HEADER + "".join(square(z, 20) for z in heights)
        return gcode(body)

    def test_an_ordinary_print_raises_nothing(self):
        path = self.stacked([0.2 * n for n in range(1, 200)])
        assert check_sequential_clearance(path, 180.0, 25.0) == ()

    def test_a_by_object_print_that_stands_taller_than_the_toolhead_is_flagged(self):
        """Z returns to the bottom partway through: a second object from the plate."""
        first = [0.2 * n for n in range(1, 200)]  # up to 39.8 mm
        path = self.stacked([*first, *first])
        findings = check_sequential_clearance(path, 180.0, 25.0)

        assert findings
        assert findings[0].rule == "sequential-print"

    def test_a_by_object_print_above_the_gantry_blocks_the_job(self):
        tall = [1.0 * n for n in range(1, 201)]  # 200 mm, above the gantry
        path = self.stacked([*tall, *tall])
        findings = check_sequential_clearance(path, 180.0, 25.0)

        assert findings[0].rule == "sequential-gantry-collision"
        assert findings[0].severity.name == "BLOCKER"

    def test_short_objects_pass_under_the_toolhead(self):
        low = [0.2 * n for n in range(1, 50)]  # under 10 mm
        path = self.stacked([*low, *low])
        assert check_sequential_clearance(path, 180.0, 25.0) == ()
