"""Parsing the slicer's result.json.

Kept separate from the subprocess call so it runs in the fast suite with no
slicer installed. The payloads below are trimmed copies of real output captured
from Bambu Studio 02.08.02.61 (see docs/research/spike-bambu-cli.md).
"""

import pytest

from modelpop.printing.bambu_slicer import parse_result_json

SUCCESS = {
    "error_string": "Success.",
    "return_code": 0,
    "layer_height": 0.20000000298023224,
    "wall_loops": 2,
    "sparse_infill_density": 20.0,
    "sliced_plates": [
        {
            "id": 1,
            "total_predication": 648.3359375,
            "generate_support_material_time": 2,
            "filament_change_times": 0,
            "filaments": [{"id": 1, "main_used_g": 12.5, "total_used_g": 14.0}],
            "objects": [
                {
                    "id": 5,
                    "name": "test.stl",
                    "triangle_count": 24,
                    "bbox": {
                        "width": 35.0,
                        "depth": 20.0,
                        "height": 20.0,
                        "x": 82.5,
                        "y": 90.0,
                        "z": 0.0,
                    },
                }
            ],
            "feature_type_times": {
                "Outer wall": 113.2,
                "Bridge": 22.7,
                "Sparse infill": 276.4,
            },
            "warning_message": "",
        }
    ],
}

INPUT_NOT_FOUND = {
    "error_string": "The input files to the slicer are not found.",
    "return_code": -3,
    "layer_height": 0.0,
}


class TestSuccessfulSlice:
    def test_reports_success(self):
        report = parse_result_json(SUCCESS)
        assert report.succeeded
        assert report.message == "Success."

    def test_extracts_the_telemetry_the_readiness_report_needs(self):
        report = parse_result_json(SUCCESS)
        assert report.predicted_seconds == pytest.approx(648.34, abs=0.01)
        assert report.layer_height is not None
        assert report.layer_height.millimetres == pytest.approx(0.2)
        assert report.wall_loops == 2
        assert report.infill_density == pytest.approx(20.0)

    def test_does_not_infer_supports_from_the_support_stage_timing(self):
        """`generate_support_material_time` is always non-zero and means nothing.

        It times the support *stage*, which runs whether or not supports are
        produced: measured at 3 for a plain cube with supports off, and 3 again
        with them on. Believing it made us report supports on a model that had
        none. The authoritative answer is `support_used` in the sliced project.
        """
        assert not parse_result_json(SUCCESS).supports_generated

    def test_formats_the_predicted_duration_for_humans(self):
        assert parse_result_json(SUCCESS).predicted_duration == "10m"
        long_print = {
            **SUCCESS,
            "sliced_plates": [{**SUCCESS["sliced_plates"][0], "total_predication": 9100}],
        }
        assert parse_result_json(long_print).predicted_duration == "2h 31m"

    def test_reports_what_the_slicer_placed(self):
        placed = parse_result_json(SUCCESS).objects
        assert len(placed) == 1
        assert placed[0].name == "test.stl"
        assert placed[0].triangle_count == 24
        assert placed[0].width.millimetres == pytest.approx(35.0)

    def test_surfaces_bridge_time_as_an_orientation_signal(self):
        # a lot of bridging on a model the user thinks is simple means bad orientation
        assert parse_result_json(SUCCESS).bridge_seconds == pytest.approx(22.7)

    def test_purge_waste_is_the_difference_between_total_and_main(self):
        # this is the number behind the AMS versus multi-plate comparison
        report = parse_result_json(SUCCESS)
        assert report.grams_used == pytest.approx(12.5)
        assert report.grams_purged == pytest.approx(1.5)

    def test_purge_waste_is_never_negative(self):
        payload = {
            **SUCCESS,
            "sliced_plates": [
                {
                    **SUCCESS["sliced_plates"][0],
                    "filaments": [{"main_used_g": 10.0, "total_used_g": 0.0}],
                }
            ],
        }
        assert parse_result_json(payload).grams_purged == 0.0


class TestFailures:
    def test_reports_a_failure_without_raising(self):
        report = parse_result_json(INPUT_NOT_FOUND)
        assert not report.succeeded

    def test_translates_the_unhelpful_minus_three_into_something_actionable(self):
        # the slicer blames a missing file; it is almost always argument quoting
        message = parse_result_json(INPUT_NOT_FOUND).message
        assert "quoting" in message

    def test_an_unknown_return_code_still_produces_a_message(self):
        report = parse_result_json({"return_code": -99, "error_string": "Something odd."})
        assert not report.succeeded
        assert report.message


class TestRobustness:
    """Real files are messy; the parser must never be the thing that crashes."""

    def test_an_empty_payload_is_a_failure_not_an_exception(self):
        assert not parse_result_json({}).succeeded

    def test_a_success_with_no_plates_still_parses(self):
        report = parse_result_json({"return_code": 0, "error_string": "Success."})
        assert report.succeeded
        assert report.objects == ()
        assert report.predicted_seconds == 0.0

    def test_missing_optional_fields_fall_back_to_defaults(self):
        report = parse_result_json({"return_code": 0, "sliced_plates": [{}]})
        assert report.succeeded
        assert report.wall_loops == 0
        assert report.feature_seconds == {}

    def test_null_filament_entries_do_not_break_the_totals(self):
        payload = {
            "return_code": 0,
            "sliced_plates": [{"filaments": [{"main_used_g": None, "total_used_g": None}]}],
        }
        assert parse_result_json(payload).grams_used == 0.0

    def test_warnings_are_carried_through_verbatim(self):
        payload = {
            "return_code": 0,
            "sliced_plates": [{"warning_message": "Object is too close to the edge."}],
        }
        assert parse_result_json(payload).warnings == ("Object is too close to the edge.",)

    def test_an_empty_warning_is_not_reported_as_a_warning(self):
        assert parse_result_json(SUCCESS).warnings == ()
