"""The CAD vocabulary, and the script it compiles to.

Two halves. The commands are pure domain values, so they are tested for what
they refuse as much as what they accept - every one of them can receive numbers
from a language model, and clamping is the boundary that keeps a hallucinated
value from reaching OCCT as a crash.

The compiler is tested by reading the script it produces. That is the failure
mode with no visible symptom: a wrong script still builds *a* shape, just not
the right one, so asserting on geometry alone would not catch it. One
integration test builds the real thing.
"""

import math

import pytest

from modelpop.application.cad_ports import Part
from modelpop.cad.feature_compiler import MAX_FEATURES, compile_document
from modelpop.domain.cad_commands import (
    MAX_COPIES,
    MAX_MM,
    MAX_OUTLINE_POINTS,
    MAX_RADIUS_MM,
    Chamfer,
    CreateBox,
    CreateCylinder,
    CreateSphere,
    EdgeSelector,
    Extrude,
    Face,
    Fillet,
    Hollow,
    Mirror,
    Move,
    Plane,
    Repeat,
    RepeatAround,
    Rotate,
    ScaleTo,
    TextOnSurface,
    command_from,
    known_commands,
)
from modelpop.domain.commands import Command, CommandBus, Document, Feature, Origin
from modelpop.domain.units import Length


def tree(*commands: Command) -> Document:
    """A document built by applying commands in order."""
    bus = CommandBus()
    for command in commands:
        bus.execute(command)
    return bus.document


def script_for(*commands: Command) -> str:
    """The build123d source a set of commands compiles to."""
    result = compile_document(tree(*commands))
    assert result.ok, result.error
    return result.unwrap()


class TestClampingHostileInput:
    """Every number here can arrive from a language model.

    An unclamped one reaches OCCT as a crash rather than a message, so the
    boundary is here and these are the cases that cross it.
    """

    def test_an_absurd_fillet_radius_is_clamped_rather_than_passed_on(self):
        assert Fillet(1e9).radius == MAX_RADIUS_MM

    def test_a_negative_dimension_becomes_the_smallest_positive_one(self):
        assert CreateBox(-50, 10, 10).width > 0

    def test_zero_is_not_accepted_as_a_dimension(self):
        """A zero-thickness solid is not a solid."""
        assert CreateCylinder(0, 10).radius > 0

    def test_not_a_number_is_caught(self):
        """NaN compares unequal to itself and slips through a naive range check."""
        assert not math.isnan(CreateBox(float("nan"), 10, 10).width)

    def test_infinity_is_caught(self):
        assert math.isfinite(CreateSphere(float("inf")).radius)

    def test_a_wall_thinner_than_a_nozzle_is_raised_to_something_printable(self):
        """A 0.05 mm wall does not exist in the slice. It is a hollow with no wall."""
        assert Hollow(0.05).wall_thickness >= 0.4

    def test_an_unprintable_text_depth_is_clamped(self):
        assert TextOnSurface("x", depth=0.001).depth >= 0.2

    def test_an_enormous_string_is_trimmed(self):
        """A thousand characters takes minutes to tessellate and prints as mush."""
        assert len(TextOnSurface("MSI " * 500).text) <= 80

    def test_an_angle_is_wrapped_rather_than_refused(self):
        assert Rotate(450).degrees == pytest.approx(90.0)

    def test_an_unknown_axis_falls_back_to_z(self):
        assert Rotate(45, "Q").axis == "Z"

    def test_a_translation_may_be_negative(self):
        """Clamping must not turn "move left" into "move right"."""
        assert Move(-20, 0, 0).dx == pytest.approx(-20.0)


class TestWhatACommandSays:
    def test_every_command_describes_itself_in_words(self):
        for command in (
            CreateBox(10, 10, 10),
            CreateCylinder(5, 10),
            CreateSphere(5),
            Fillet(2),
            Chamfer(2),
            Hollow(1.5),
            Move(1, 2, 3),
            Rotate(90),
            ScaleTo(Length.mm(50)),
            TextOnSurface("hi"),
        ):
            assert command.describe()
            assert command.describe() != command.name

    def test_a_description_names_the_edges_it_touches(self):
        assert "vertical" in Fillet(2, EdgeSelector.VERTICAL).describe()

    def test_a_hollow_says_which_face_is_left_open(self):
        assert "bottom" in Hollow(1.5, Face.BOTTOM).describe()

    def test_a_sealed_hollow_does_not_claim_an_opening(self):
        assert "open" not in Hollow(1.5).describe()

    def test_a_size_is_described_in_the_units_it_was_given(self):
        assert "152" in ScaleTo(Length.inches(6)).describe()


class TestRecordingAndRebuilding:
    def test_a_command_survives_being_recorded_and_read_back(self):
        original = Fillet(3.5, EdgeSelector.TOP)
        restored = command_from(original.to_feature())

        assert restored is not None
        assert restored.parameters == original.parameters

    def test_every_command_in_the_vocabulary_round_trips(self):
        for command in (
            CreateBox(10, 20, 30),
            CreateCylinder(5, 10),
            CreateSphere(7),
            Fillet(2, EdgeSelector.VERTICAL),
            Chamfer(1, EdgeSelector.TOP),
            Hollow(1.6, Face.BOTTOM),
            Move(1, 2, 3),
            Rotate(90, "X"),
            ScaleTo(Length.mm(50)),
            TextOnSurface("MSI", Face.FRONT, 12, 1.5, raised=False),
        ):
            restored = command_from(command.to_feature())
            assert restored is not None, command.name
            assert restored.parameters == command.parameters, command.name

    def test_a_feature_from_a_newer_version_is_skipped_rather_than_fatal(self):
        """A document saved by a newer build must still open."""
        assert command_from(Feature("warp-drive", {"factor": 9})) is None

    def test_a_feature_with_missing_parameters_is_skipped_rather_than_fatal(self):
        assert command_from(Feature("fillet", {})) is None

    def test_the_vocabulary_is_enumerable(self):
        """What a language model is handed, and what a loader checks against."""
        names = known_commands()
        assert "fillet" in names
        assert "text-on-surface" in names
        assert names == tuple(sorted(names))

    def test_the_document_hash_ignores_when_it_was_built(self):
        """Two models built the same way by different routes must match."""
        assert tree(CreateBox(10, 10, 10)).content_hash == tree(CreateBox(10, 10, 10)).content_hash

    def test_a_different_model_hashes_differently(self):
        assert tree(CreateBox(10, 10, 10)).content_hash != tree(CreateBox(11, 10, 10)).content_hash

    def test_who_made_a_change_is_recorded(self):
        """So "undo everything the assistant just did" is possible."""
        bus = CommandBus()
        bus.execute(CreateBox(10, 10, 10), Origin.USER)
        bus.execute(Fillet(1), Origin.ASSISTANT)

        origins = [f.origin for f in bus.document]
        assert origins == [Origin.USER, Origin.ASSISTANT]


class TestCompilingToAScript:
    def test_a_box_becomes_a_box(self):
        assert "Box(40.0, 40.0, 60.0)" in script_for(CreateBox(40, 40, 60))

    def test_the_script_names_each_feature_in_a_comment(self):
        """So "show me what this model actually is" reads as prose."""
        source = script_for(CreateBox(10, 10, 10), Fillet(2))
        assert "# 1. Add a 10 x 10 x 10 mm box" in source
        assert "# 2. Round all edges by 2 mm" in source

    def test_features_are_applied_in_order(self):
        source = script_for(CreateBox(10, 10, 10), Fillet(2), Chamfer(1))
        assert source.index("fillet(") < source.index("chamfer(")

    def test_a_second_primitive_is_added_rather_than_replacing_the_first(self):
        """Someone adding a cylinder to a box means "and also", not "instead"."""
        source = script_for(CreateBox(40, 40, 10), CreateCylinder(8, 30))
        assert "result = result + Cylinder" in source

    def test_each_edge_selector_compiles_to_a_different_filter(self):
        filters = {
            script_for(CreateBox(10, 10, 10), Fillet(1, selector)).split("fillet(")[1]
            for selector in EdgeSelector
        }
        assert len(filters) == len(EdgeSelector), "two selectors compiled the same"

    def test_edges_are_selected_by_name_not_by_index(self):
        """Edge numbering is not stable across a rebuild. "Edge 7" would round a
        different edge once an earlier feature changed."""
        source = script_for(CreateBox(10, 10, 10), Fillet(1, EdgeSelector.VERTICAL))
        assert "Axis.Z" in source
        assert "edges()[7]" not in source

    def test_a_sealed_hollow_opens_nothing(self):
        assert "openings=[]" in script_for(CreateBox(10, 10, 10), Hollow(1.5))

    def test_an_opened_hollow_names_the_face(self):
        source = script_for(CreateBox(10, 10, 10), Hollow(1.5, Face.BOTTOM))
        assert "sort_by(Axis.Z)[0]" in source

    def test_scaling_is_computed_at_rebuild_time_not_baked_in(self):
        """Scaling to six inches must still give six inches after an earlier
        feature changed the shape."""
        source = script_for(CreateBox(10, 10, 10), ScaleTo(Length.inches(6)))
        assert "bounding_box()" in source
        assert "152.4" in source

    def test_text_is_escaped_rather_than_interpolated_raw(self):
        """The string came from a prompt. A quote in it would end the literal."""
        nasty = TextOnSurface("it's \" here")
        source = script_for(CreateBox(30, 30, 30), nasty)

        compile(source, "<generated>", "exec")  # would raise on a broken literal

    def test_an_injection_attempt_stays_inside_the_string(self):
        attack = TextOnSurface("'); import os; os.system('echo pwned')  #")
        source = script_for(CreateBox(30, 30, 30), attack)

        compile(source, "<generated>", "exec")
        assert "import os" not in source.replace(repr(attack.text), "")

    def test_raised_text_is_added_and_engraved_text_is_cut(self):
        raised = script_for(CreateBox(30, 30, 30), TextOnSurface("x", raised=True))
        cut = script_for(CreateBox(30, 30, 30), TextOnSurface("x", raised=False))

        assert "result + _relief" in raised
        assert "result - _relief" in cut

    def test_the_script_is_valid_python(self):
        source = script_for(
            CreateBox(50, 40, 150),
            Fillet(4, EdgeSelector.VERTICAL),
            Hollow(2.0, Face.BOTTOM),
            TextOnSurface("MSI", Face.FRONT, 18, 1.5),
            ScaleTo(Length.inches(6)),
        )
        compile(source, "<generated>", "exec")


class TestWhatTheCompilerRefuses:
    def test_an_empty_model_says_there_is_nothing_to_build(self):
        result = compile_document(Document())
        assert not result.ok
        assert "nothing to build" in result.error

    def test_an_operation_with_nothing_to_operate_on_is_refused(self):
        """A fillet before any solid exists is a mistake worth catching here
        rather than as a kernel traceback."""
        result = compile_document(tree(Fillet(2)))
        assert not result.ok

    def test_a_runaway_tree_is_refused_rather_than_rebuilt(self):
        many = tree(*[CreateBox(1, 1, 1) for _ in range(MAX_FEATURES + 1)])
        result = compile_document(many)

        assert not result.ok
        assert str(MAX_FEATURES) in result.detail

    def test_an_unknown_feature_is_skipped_and_said_out_loud(self):
        """Skipping silently would give the user a model that is quietly wrong."""
        document = tree(CreateBox(10, 10, 10)).with_feature(Feature("warp-drive", {}))
        source = compile_document(document).unwrap()

        assert "Box(" in source
        assert "warp-drive" in source
        assert "not understood" in source

    def test_a_model_of_only_unknown_features_is_refused(self):
        document = Document(features=(Feature("warp-drive", {}),))
        result = compile_document(document)

        assert not result.ok
        assert "warp-drive" in result.detail

    def test_a_suppressed_feature_is_left_out(self):
        document = tree(CreateBox(10, 10, 10), Fillet(99))
        suppressed = Document(
            features=(document.features[0], Feature("fillet", {"radius": 99}, suppressed=True))
        )
        assert "fillet(" not in compile_document(suppressed).unwrap()


class TestCuttingAndPlacing:
    """Holes, pockets and off-centre parts.

    Cutting a cylinder is how a hole is made, which printed parts need more
    than any other operation.
    """

    def test_a_cut_cylinder_is_described_as_a_hole(self):
        """Because that is what the user calls it."""
        assert "8 mm hole" in CreateCylinder(4, 20, cut=True).describe()

    def test_an_added_cylinder_is_still_described_as_a_cylinder(self):
        assert "cylinder" in CreateCylinder(4, 20).describe()

    def test_a_placed_shape_says_where_it_is(self):
        assert "(10, 0, 5)" in CreateBox(5, 5, 5, x=10, z=5).describe()

    def test_a_centred_shape_does_not_claim_a_position(self):
        assert "at (" not in CreateBox(5, 5, 5).describe()

    def test_a_cut_compiles_to_a_subtraction(self):
        source = script_for(CreateBox(40, 40, 20), CreateCylinder(4, 30, cut=True))
        assert "result = result - Cylinder" in source

    def test_an_addition_compiles_to_a_union(self):
        source = script_for(CreateBox(40, 40, 20), CreateCylinder(4, 30))
        assert "result = result + Cylinder" in source

    def test_a_placement_compiles_to_a_translation(self):
        source = script_for(CreateBox(40, 40, 20), CreateCylinder(4, 30, x=10, y=5, cut=True))
        assert "Pos(10.0, 5.0, 0.0) * Cylinder" in source

    def test_a_centred_shape_is_not_wrapped_in_a_translation(self):
        """Noise in the script the user reads, for no effect."""
        assert "Pos(" not in script_for(CreateBox(10, 10, 10))

    def test_cutting_with_nothing_to_cut_from_is_refused(self):
        """An empty model is not a model, and a cut from nothing is empty."""
        result = compile_document(tree(CreateCylinder(4, 30, cut=True)))
        assert not result.ok

    def test_a_placement_is_clamped_but_may_be_negative(self):
        assert CreateBox(5, 5, 5, x=-30).x == pytest.approx(-30.0)
        assert abs(CreateBox(5, 5, 5, x=1e9).x) <= 1000

    def test_placement_and_cutting_survive_a_round_trip(self):
        original = CreateCylinder(4, 30, x=10, y=-5, z=2, cut=True)
        restored = command_from(original.to_feature())

        assert restored is not None
        assert restored.parameters == original.parameters

    def test_a_file_written_before_placement_existed_still_opens(self):
        """Old projects have no x, y, z or cut. They must default, not fail."""
        old = Feature("create-box", {"width": 10.0, "depth": 10.0, "height": 10.0})
        restored = command_from(old)

        assert restored is not None
        assert restored.parameters["x"] == 0.0
        assert restored.parameters["cut"] is False


class TestSplittingIntoColours:
    """Raised lettering is a second colour waiting to happen.

    The compiled scripts are asserted on directly, because a wrong split still
    produces *a* pair of shapes and geometry alone would not catch it.
    """

    def lettered(self):
        return tree(
            CreateBox(60, 20, 40),
            Fillet(2, EdgeSelector.VERTICAL),
            TextOnSurface("MSI", Face.FRONT, 14, 1.5),
        )

    def test_the_body_leaves_the_lettering_out(self):
        source = compile_document(self.lettered(), Part.BODY).unwrap()
        assert "Text(" not in source
        assert "Box(" in source

    def test_the_lettering_is_only_the_lettering(self):
        source = compile_document(self.lettered(), Part.DECORATION).unwrap()
        assert source.rstrip().endswith("result = _decoration")

    def test_the_lettering_still_needs_the_body_to_sit_on(self):
        """It is placed on a face, so the face has to exist first."""
        source = compile_document(self.lettered(), Part.DECORATION).unwrap()
        assert "Box(" in source
        assert "faces()" in source

    def test_the_whole_thing_is_still_one_object(self):
        source = compile_document(self.lettered(), Part.WHOLE).unwrap()
        assert "result = result + _relief" in source
        assert not source.rstrip().endswith("result = _decoration")

    def test_several_letters_accumulate_rather_than_replacing_each_other(self):
        document = tree(
            CreateBox(60, 20, 40),
            TextOnSurface("MSI", Face.FRONT, 12, 1.5),
            TextOnSurface("P2S", Face.BACK, 12, 1.5),
        )
        source = compile_document(document, Part.DECORATION).unwrap()
        assert source.count("_decoration = _relief if _decoration is None") == 2

    def test_engraved_text_is_not_a_second_colour(self):
        """It is a hole in the body. There is no second solid to print."""
        document = tree(
            CreateBox(60, 20, 40),
            TextOnSurface("MSI", Face.FRONT, 12, 1.5, raised=False),
        )
        result = compile_document(document, Part.DECORATION)

        assert not result.ok
        assert "nothing to print in a second colour" in result.error

    def test_a_model_with_no_lettering_is_refused_with_a_reason(self):
        result = compile_document(tree(CreateBox(10, 10, 10)), Part.DECORATION)
        assert not result.ok
        assert "Add raised text" in result.detail

    def test_the_body_of_an_unlettered_model_is_just_the_model(self):
        plain = tree(CreateBox(10, 10, 10), Fillet(1))
        assert compile_document(plain, Part.BODY).unwrap() == compile_document(plain).unwrap()

    def test_every_part_compiles_to_valid_python(self):
        for part in Part:
            compile(compile_document(self.lettered(), part).unwrap(), "<generated>", "exec")


class TestOutlinesAndExtrusion:
    """Drawing a profile and giving it thickness.

    An outline arrives from a mouse or from a language model with equal ease,
    so the same rules apply to both: finite, bounded, capped in length, and
    free of the repeated points that OCCT rejects without saying which point
    it meant.
    """

    SQUARE = ((0, 0), (10, 0), (10, 10), (0, 10))

    def test_a_simple_outline_survives_intact(self):
        assert Extrude(self.SQUARE, 5).points == (
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
            (0.0, 10.0),
        )

    def test_a_repeated_point_is_dropped(self):
        """A zero-length edge is a kernel failure with no useful message."""
        outline = Extrude(((0, 0), (10, 0), (10, 0), (10, 10)), 5).points
        assert outline == ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0))

    def test_a_closing_point_that_repeats_the_first_is_redundant(self):
        """The outline closes itself; leaving it in makes another dead edge."""
        assert Extrude(((0, 0), (10, 0), (10, 10), (0, 0)), 5).points == (
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
        )

    def test_points_are_clamped_to_the_buildable_world(self):
        for x, y in Extrude(((0, 0), (1e9, 0), (0, -1e9)), 5).points:
            assert abs(x) <= MAX_MM and abs(y) <= MAX_MM

    def test_nonsense_points_are_skipped_rather_than_crashing(self):
        outline = Extrude(((0, 0), "nope", (10, 0), None, (10, 10)), 5).points
        assert outline == ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0))

    def test_a_runaway_outline_is_capped(self):
        many = tuple((i * 0.5, i) for i in range(MAX_OUTLINE_POINTS * 3))
        assert len(Extrude(many, 5).points) <= MAX_OUTLINE_POINTS

    def test_the_height_is_clamped_like_every_other_length(self):
        assert Extrude(self.SQUARE, 1e9).height <= MAX_MM
        assert Extrude(self.SQUARE, -4).height > 0

    def test_too_few_points_is_not_closed_enough(self):
        assert not Extrude(((0, 0), (10, 0)), 5).is_closed_enough
        assert Extrude(self.SQUARE, 5).is_closed_enough

    def test_it_says_what_it_is(self):
        assert "Extrude a 4-point outline 5 mm" in Extrude(self.SQUARE, 5).describe()
        assert Extrude(self.SQUARE, 5, Plane.XZ, cut=True).describe().startswith("Cut")

    def test_it_survives_a_round_trip_through_the_document(self):
        original = Extrude(self.SQUARE, 5, Plane.YZ, cut=True)
        rebuilt = command_from(tree(CreateBox(20, 20, 20), original).active_features[-1])

        assert rebuilt == original

    def test_it_compiles_to_a_closed_profile_given_thickness(self):
        source = script_for(Extrude(self.SQUARE, 5))

        assert "Polyline([(-5, -5), (5, -5), (5, 5), (-5, 5)], close=True)" in source
        assert "make_face(Plane.XY * _outline)" in source
        assert "result = _solid" in source

    def test_the_profile_is_centred_on_the_origin_like_every_other_shape(self):
        """Measured, not assumed.

        An outline grown upwards from where it was drawn cut only half way
        through a 10 mm plate, because the plate is centred and straddles the
        plane. Everything in this vocabulary is centred, so an outline is too,
        and the corners a user types describe a shape rather than a position.
        """
        source = script_for(Extrude(((0, 0), (60, 0), (60, 40), (0, 40)), 10))

        assert "Polyline([(-30, -20), (30, -20), (30, 20), (-30, 20)]" in source
        assert "extrude(_profile, amount=5.0, both=True)" in source

    def test_the_plane_reaches_the_script(self):
        assert "Plane.XZ * _outline" in script_for(Extrude(self.SQUARE, 5, Plane.XZ))
        assert "Plane.YZ * _outline" in script_for(Extrude(self.SQUARE, 5, Plane.YZ))

    def test_a_second_profile_is_added_to_the_first(self):
        source = script_for(Extrude(self.SQUARE, 5), Extrude(self.SQUARE, 3))
        assert "result = result + _solid" in source

    def test_a_cut_profile_removes_material(self):
        source = script_for(CreateBox(40, 40, 10), Extrude(self.SQUARE, 20, cut=True))
        assert "result = result - _solid" in source

    def test_a_cut_cannot_be_the_first_thing_in_the_model(self):
        """There is nothing to cut it out of yet."""
        result = compile_document(tree(Extrude(self.SQUARE, 5, cut=True)))
        assert not result.ok

    def test_an_outline_that_encloses_nothing_is_refused(self):
        result = compile_document(tree(Extrude(((0, 0), (10, 0)), 5)))
        assert not result.ok

    def test_it_compiles_to_valid_python(self):
        compile(
            script_for(CreateBox(40, 40, 10), Extrude(self.SQUARE, 20, cut=True)),
            "<generated>",
            "exec",
        )

    def test_it_is_part_of_the_vocabulary_offered_to_a_model(self):
        assert "extrude" in known_commands()


class TestMirroringAndPatterns:
    """Symmetry and repetition - the two things that make a part quick to model.

    The patterns are asserted on the script rather than on geometry because the
    way they fail is silent: a pattern that copies the built solid instead of
    re-emitting the shape turns six drilled holes into six plugs, and the
    result is still a valid, buildable, wrong model.
    """

    def test_a_mirror_keeps_both_halves_by_default(self):
        source = script_for(CreateBox(20, 40, 10, x=10), Mirror())
        assert "result = result + mirror(result, about=Plane.YZ)" in source

    def test_a_mirror_can_replace_the_original_instead(self):
        source = script_for(CreateBox(20, 40, 10, x=10), Mirror(Plane.YZ, keep_original=False))
        assert "result = mirror(result, about=Plane.YZ)" in source
        assert "result = result + mirror" not in source

    def test_the_mirror_plane_reaches_the_script(self):
        assert "about=Plane.XZ" in script_for(CreateBox(10, 10, 10), Mirror(Plane.XZ))

    def test_a_row_repeats_the_shape_before_it(self):
        source = script_for(CreateBox(40, 40, 6), CreateCylinder(2.5, 20, x=-30), Repeat(4, dx=20))

        assert source.count("Cylinder(2.5, 20.0)") == 4, "the original and three copies"
        assert "Pos(20, 0, 0) * (Pos(-30.0, 0.0, 0.0) * Cylinder(2.5, 20.0))" in source
        assert "Pos(60, 0, 0) * (Pos(-30.0, 0.0, 0.0) * Cylinder(2.5, 20.0))" in source

    def test_repeating_a_cut_cuts_every_copy(self):
        """The failure this test exists for makes plugs instead of holes."""
        source = script_for(
            CreateBox(40, 40, 6), CreateCylinder(2.5, 20, x=-30, cut=True), Repeat(3, dx=20)
        )
        assert source.count("result = result - ") == 3
        assert "result = result + " not in source

    def test_a_ring_spaces_copies_over_a_full_turn(self):
        source = script_for(CreateCylinder(30, 8), CreateCylinder(3, 20, x=20), RepeatAround(4))

        assert "Rot(0, 0, 90)" in source
        assert "Rot(0, 0, 180)" in source
        assert "Rot(0, 0, 270)" in source
        assert "Rot(0, 0, 360)" not in source, "that is back where it started"

    def test_a_ring_can_turn_about_another_axis(self):
        source = script_for(CreateBox(10, 10, 10), CreateBox(4, 4, 4, z=20), RepeatAround(4, "X"))
        assert "Rot(90, 0, 0)" in source

    def test_an_extruded_profile_patterns_like_anything_else(self):
        source = script_for(Extrude(((0, 0), (10, 0), (10, 10)), 5), Repeat(3, dy=15))
        assert source.count("_outline = Polyline(") == 3
        assert "_solid = Pos(0, 15, 0) * _solid" in source

    def test_a_pattern_with_nothing_before_it_is_refused(self):
        assert not compile_document(tree(Repeat(4, dx=10))).ok

    def test_a_pattern_after_an_operation_still_repeats_the_shape(self):
        """A fillet between the hole and the pattern must not break the link."""
        source = script_for(
            CreateBox(40, 40, 6),
            CreateCylinder(2.5, 20, x=-10, cut=True),
            Fillet(1, EdgeSelector.TOP),
            Repeat(2, dx=20),
        )
        assert source.count("Cylinder(2.5, 20.0)") == 2

    def test_a_single_copy_says_so_rather_than_emitting_nothing(self):
        source = script_for(CreateBox(10, 10, 10), Repeat(1, dx=20))
        assert "one copy is the shape itself" in source

    def test_the_count_is_capped(self):
        assert Repeat(10_000, dx=5).times == MAX_COPIES
        assert RepeatAround(10_000).times == MAX_COPIES

    def test_a_nonsense_count_falls_back_rather_than_crashing(self):
        assert Repeat("lots", dx=5).times == 2
        assert Repeat(0, dx=5).times == 1

    def test_copies_on_top_of_each_other_are_recognised(self):
        assert not Repeat(4).goes_anywhere
        assert Repeat(4, dz=0.5).goes_anywhere

    def test_they_all_say_what_they_do(self):
        assert "Mirror across the side plane" in Mirror().describe()
        assert Repeat(4, dx=20).describe() == "Repeat the last shape 4 times, (20, 0, 0) mm apart"
        assert "6 copies" in RepeatAround(6).describe()

    def test_they_all_survive_a_round_trip_through_the_document(self):
        for command in (
            Mirror(Plane.XZ, keep_original=False),
            Repeat(3, dy=8),
            RepeatAround(5, "Y"),
        ):
            document = tree(CreateBox(20, 20, 20), command)
            assert command_from(document.active_features[-1]) == command

    def test_they_are_part_of_the_vocabulary_offered_to_a_model(self):
        for name in ("mirror", "repeat", "repeat-around"):
            assert name in known_commands()

    def test_every_pattern_compiles_to_valid_python(self):
        source = script_for(
            CreateCylinder(30, 8), CreateCylinder(3, 20, x=20, cut=True), RepeatAround(6), Mirror()
        )
        compile(source, "<generated>", "exec")
