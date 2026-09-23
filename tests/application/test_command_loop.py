"""Changing a model by describing the change, with a scripted model.

This is ADR-0001's claim under test: a language model asks for typed commands,
those commands go on the same bus a toolbar click uses, and the result is
undoable without anything special being written for it.

Most of these are about what the model sends back being *wrong*. A model that
replies with prose, invents an operation, sends a string where a number belongs
or asks for thirty changes are all ordinary Tuesday outcomes, and every one must
end with a model that still builds.
"""

import json
from dataclasses import dataclass, field

from modelpop.application.ai_ports import ChatProvider, Completion, Conversation, ModelChoice
from modelpop.application.modelling import ModellingSession
from modelpop.domain.cad_commands import CreateBox
from modelpop.domain.commands import Origin
from modelpop.domain.result import Result, failure, success
from modelpop.generation.command_loop import edit_by_description
from modelpop.generation.command_prompt import (
    MAX_COMMANDS,
    SYSTEM_PROMPT,
    build_edit_request,
    build_new_request,
    read_commands,
)

from .test_modelling import FakeCompiler


@dataclass
class ScriptedModel:
    """A provider that says whatever the test tells it to."""

    replies: list[str] = field(default_factory=list)
    configured: bool = True
    error: str = ""
    refused: bool = False
    seen: list[Conversation] = field(default_factory=list)

    def is_configured(self) -> bool:
        return self.configured

    def describe(self) -> str:
        return "a scripted model"

    def complete(self, conversation: Conversation, choice: ModelChoice) -> Result[Completion]:
        self.seen.append(conversation)
        if self.error:
            return failure(self.error)
        text = self.replies.pop(0) if self.replies else "[]"
        return success(
            Completion(
                text=text, refused=self.refused, refusal_category="policy" if self.refused else ""
            )
        )


def commands(*entries: dict) -> str:
    """A well-formed reply asking for these operations."""
    return json.dumps(list(entries))


def started(**kwargs) -> ModellingSession:
    """A session with a box already in it."""
    session = ModellingSession(FakeCompiler(**kwargs))
    session.apply(CreateBox(40, 40, 60))
    return session


class TestTheFakeMatchesThePort:
    def test_the_scripted_model_satisfies_the_port(self):
        assert isinstance(ScriptedModel(), ChatProvider)


class TestThePrompt:
    def test_the_vocabulary_is_generated_from_the_code(self):
        """A prompt typed out by hand drifts, and the drift is silent."""
        from modelpop.domain.cad_commands import known_commands

        for name in known_commands():
            assert name in SYSTEM_PROMPT, f"{name} is missing from the prompt"

    def test_the_model_is_told_not_to_write_code(self):
        assert "do not write code" in SYSTEM_PROMPT.lower()

    def test_the_model_is_told_the_units(self):
        """Every number it sends is a millimetre, and it will be given inches."""
        assert "millimetres" in SYSTEM_PROMPT
        assert "152.4" in SYSTEM_PROMPT

    def test_the_model_is_told_to_refuse_rather_than_approximate(self):
        """Matched on the whole prompt with newlines flattened, because the
        instruction wraps and a literal match would break on rewrapping."""
        flat = " ".join(SYSTEM_PROMPT.split())
        assert "empty array" in flat
        assert "Do not approximate" in flat

    def test_the_request_includes_the_current_model(self):
        """ "Make it taller" means nothing without knowing what "it" is."""
        message = build_edit_request("make it taller", "1. Box 40 x 40 x 60 mm")
        assert "Box 40 x 40 x 60" in message
        assert "make it taller" in message

    def test_an_empty_model_is_described_rather_than_left_blank(self):
        assert "nothing yet" in build_edit_request("start with a cube", "")


class TestReadingWhatTheModelSent:
    def test_a_plain_json_array_is_read(self):
        parsed = read_commands(commands({"name": "fillet", "parameters": {"radius": 3}}))
        found, _ = parsed.unwrap()
        assert len(found) == 1
        assert found[0].name == "fillet"

    def test_a_fenced_reply_is_read(self):
        body = commands({"name": "fillet", "parameters": {"radius": 3}})
        assert read_commands(f"```json\n{body}\n```").ok

    def test_a_reply_with_a_sentence_in_front_is_still_read(self):
        body = commands({"name": "fillet", "parameters": {"radius": 3}})
        assert read_commands(f"Sure, here you go:\n{body}").ok

    def test_prose_with_no_json_is_reported_rather_than_guessed_at(self):
        result = read_commands("I would round the corners a bit.")
        assert not result.ok
        assert "usable JSON" in result.error

    def test_an_empty_reply_is_reported(self):
        assert not read_commands("").ok

    def test_an_empty_array_means_it_cannot_be_done(self):
        """The prompt asks for this explicitly, so it is an answer, not a failure."""
        result = read_commands("[]")
        assert not result.ok
        assert "cannot be done" in result.error

    def test_an_object_instead_of_an_array_is_reported(self):
        result = read_commands('{"name": "fillet"}')
        assert not result.ok
        assert "wrong shape" in result.error

    def test_an_invented_operation_is_discarded_and_named(self):
        """Quietly doing less than asked is worse than saying what was dropped."""
        body = commands(
            {"name": "fillet", "parameters": {"radius": 3}},
            {"name": "apply-chrome", "parameters": {"shine": 11}},
        )
        found, discarded = read_commands(body).unwrap()

        assert len(found) == 1
        assert discarded == ("apply-chrome",)

    def test_a_reply_of_only_invented_operations_is_refused(self):
        result = read_commands(commands({"name": "apply-chrome", "parameters": {}}))
        assert not result.ok
        assert "apply-chrome" in result.detail

    def test_a_string_where_a_number_belongs_is_discarded(self):
        result = read_commands(commands({"name": "fillet", "parameters": {"radius": "three"}}))
        assert not result.ok

    def test_missing_parameters_are_discarded(self):
        assert not read_commands(commands({"name": "fillet", "parameters": {}})).ok

    def test_entries_that_are_not_objects_are_discarded(self):
        body = json.dumps(["fillet", 3, None, {"name": "fillet", "parameters": {"radius": 2}}])
        found, discarded = read_commands(body).unwrap()

        assert len(found) == 1
        assert len(discarded) == 3

    def test_a_runaway_reply_is_capped(self):
        """ "Make it nicer" must not come back as thirty changes to undo."""
        body = commands(*[{"name": "fillet", "parameters": {"radius": 1}}] * 40)
        found, _ = read_commands(body).unwrap()
        assert len(found) == MAX_COMMANDS

    def test_a_hostile_value_is_clamped_rather_than_passed_through(self):
        """The clamping lives on the command, and this is the path that needs it."""
        found, _ = read_commands(
            commands({"name": "fillet", "parameters": {"radius": 1e12}})
        ).unwrap()
        assert found[0].parameters["radius"] < 1000

    def test_nothing_from_the_model_is_ever_executed(self):
        """The whole point: a string from a model reaches a constructor, not exec."""
        body = commands({"name": "text-on-surface", "parameters": {"text": "'); import os  #"}})
        found, _ = read_commands(body).unwrap()
        assert found[0].parameters["text"] == "'); import os  #"


class TestApplyingWhatWasAsked:
    def test_a_described_change_reaches_the_feature_tree(self):
        session = started()
        model = ScriptedModel([commands({"name": "fillet", "parameters": {"radius": 3}})])

        run = edit_by_description(session, "round the corners", model).unwrap()

        assert run.changed_anything
        assert len(session.state.features) == 2

    def test_the_change_is_recorded_as_the_assistants(self):
        """So the tree can show it, and so it can be told apart from the user's."""
        session = started()
        model = ScriptedModel([commands({"name": "fillet", "parameters": {"radius": 3}})])
        edit_by_description(session, "round the corners", model)

        assert session.state.features[-1].origin is Origin.ASSISTANT

    def test_an_ai_edit_is_undoable_with_no_special_case(self):
        session = started()
        model = ScriptedModel([commands({"name": "fillet", "parameters": {"radius": 3}})])
        edit_by_description(session, "round the corners", model)

        assert session.state.can_undo
        session.undo()
        assert len(session.state.features) == 1

    def test_several_changes_are_applied_in_order(self):
        session = started()
        model = ScriptedModel(
            [
                commands(
                    {"name": "fillet", "parameters": {"radius": 3}},
                    {"name": "hollow", "parameters": {"wall_thickness": 2, "opening": "bottom"}},
                )
            ]
        )
        run = edit_by_description(session, "round it and hollow it", model).unwrap()

        assert len(run.applied) == 2
        assert "Hollow" in run.applied[1]

    def test_one_refused_change_does_not_lose_the_others(self):
        """A model can get the fillet right and the wall thickness wrong."""
        session = started(refuse_containing="hollow")
        model = ScriptedModel(
            [
                commands(
                    {"name": "fillet", "parameters": {"radius": 3}},
                    {"name": "hollow", "parameters": {"wall_thickness": 2}},
                )
            ]
        )
        run = edit_by_description(session, "round it and hollow it", model).unwrap()

        assert len(run.applied) == 1
        assert len(run.refused) == 1
        assert len(session.state.features) == 2

    def test_the_model_still_builds_after_a_partial_failure(self):
        session = started(refuse_containing="hollow")
        model = ScriptedModel([commands({"name": "hollow", "parameters": {"wall_thickness": 2}})])
        edit_by_description(session, "hollow it", model)

        assert session.state.has_geometry

    def test_the_summary_says_what_did_not_happen(self):
        session = started(refuse_containing="hollow")
        model = ScriptedModel(
            [
                commands(
                    {"name": "fillet", "parameters": {"radius": 3}},
                    {"name": "hollow", "parameters": {"wall_thickness": 2}},
                    {"name": "apply-chrome", "parameters": {}},
                )
            ]
        )
        run = edit_by_description(session, "do three things", model).unwrap()
        summary = run.summary()

        assert "Applied 1" in summary
        assert "could not be made" in summary
        assert "does not exist" in summary

    def test_the_run_log_accounts_for_every_operation(self):
        session = started(refuse_containing="hollow")
        model = ScriptedModel(
            [
                commands(
                    {"name": "fillet", "parameters": {"radius": 3}},
                    {"name": "hollow", "parameters": {"wall_thickness": 2}},
                )
            ]
        )
        detail = edit_by_description(session, "two things", model).unwrap().detail()

        assert "Applied:" in detail
        assert "Refused:" in detail
        assert "Spent about $" in detail


class TestWhenItCannotRun:
    def test_an_empty_instruction_is_refused_before_any_call(self):
        model = ScriptedModel()
        assert not edit_by_description(started(), "   ", model).ok
        assert model.seen == []

    def test_an_unconfigured_provider_says_what_to_do(self):
        result = edit_by_description(started(), "round it", ScriptedModel(configured=False))
        assert not result.ok
        assert "Settings" in result.detail

    def test_a_provider_error_is_reported_rather_than_raised(self):
        result = edit_by_description(
            started(), "round it", ScriptedModel(error="the service is down")
        )
        assert not result.ok

    def test_a_refusal_from_the_model_is_reported_as_one(self):
        """Not as a parse failure, which is what the text would look like."""
        model = ScriptedModel(["I won't help with that."], refused=True)
        result = edit_by_description(started(), "round it", model)

        assert not result.ok
        assert "declined" in result.error

    def test_the_current_tree_is_sent_to_the_model(self):
        model = ScriptedModel([commands({"name": "fillet", "parameters": {"radius": 1}})])
        edit_by_description(started(), "round it", model)

        sent = model.seen[0].messages[0].text
        assert "40 x 40 x 60 mm box" in sent

    def test_the_size_is_sent_so_relative_requests_make_sense(self):
        model = ScriptedModel([commands({"name": "fillet", "parameters": {"radius": 1}})])
        edit_by_description(started(), "make it half as tall", model)

        assert "10.0 mm" in model.seen[0].messages[0].text


class TestBuildingAPartFromWords:
    """A description that starts a part rather than changing one.

    The point of routing it here rather than through the code-generation loop:
    what comes back is an ordinary feature tree, so the toolbar can carry on
    refining it. A generated *script* can only be edited by asking a model
    again, which is the divergence ADR-0009 recorded as the cost of that path.
    """

    def empty(self) -> ModellingSession:
        return ModellingSession(FakeCompiler())

    def test_an_empty_model_is_asked_to_build_rather_than_to_change(self):
        """Asked to change nothing into a bracket, a model tends to stall."""
        session = self.empty()
        model = ScriptedModel(
            [
                commands(
                    {"name": "create-box", "parameters": {"width": 80, "depth": 30, "height": 5}}
                )
            ]
        )

        edit_by_description(session, "a bracket 80 mm long", model)

        asked = model.seen[0].messages[0].text
        assert "Build one from nothing" in asked
        assert "a bracket 80 mm long" in asked
        assert "Change it so that" not in asked

    def test_a_part_with_something_in_it_is_still_asked_to_change(self):
        session = started()
        model = ScriptedModel([commands({"name": "fillet", "parameters": {"radius": 3}})])

        edit_by_description(session, "round the corners", model)

        asked = model.seen[0].messages[0].text
        assert "Change it so that" in asked
        assert "Build one from nothing" not in asked

    def test_the_result_is_a_feature_tree_the_toolbar_can_go_on_editing(self):
        session = self.empty()
        model = ScriptedModel(
            [
                commands(
                    {"name": "create-box", "parameters": {"width": 80, "depth": 30, "height": 5}},
                    {
                        "name": "create-cylinder",
                        "parameters": {"radius": 2, "height": 20, "x": -30, "cut": True},
                    },
                    {"name": "repeat", "parameters": {"times": 4, "dx": 20}},
                )
            ]
        )

        run = edit_by_description(session, "a bracket with four holes", model).unwrap()

        assert run.changed_anything
        assert len(session.state.features) == 3
        assert session.state.can_undo, "and every step of it undoes"

    def test_a_built_part_is_recorded_as_the_assistants(self):
        session = self.empty()
        model = ScriptedModel(
            [
                commands(
                    {"name": "create-box", "parameters": {"width": 10, "depth": 10, "height": 10}}
                )
            ]
        )

        edit_by_description(session, "a cube", model)

        assert session.state.features[0].origin is Origin.ASSISTANT

    def test_a_step_the_kernel_refuses_does_not_lose_the_rest_of_the_part(self):
        """A part half built is more useful than a part not built at all."""
        session = ModellingSession(FakeCompiler(refuse_containing="hollow"))
        model = ScriptedModel(
            [
                commands(
                    {"name": "create-box", "parameters": {"width": 40, "depth": 40, "height": 40}},
                    {"name": "hollow", "parameters": {"wall_thickness": 2}},
                    {"name": "fillet", "parameters": {"radius": 3}},
                )
            ]
        )

        run = edit_by_description(session, "a hollow rounded box", model).unwrap()

        assert len(run.applied) == 2, "the box and the fillet"
        assert len(run.refused) == 1
        assert session.state.measurements is not None, "and what is left still builds"

    def test_asking_for_nothing_is_still_refused(self):
        assert not edit_by_description(self.empty(), "   ", ScriptedModel()).ok

    def test_the_new_part_request_tells_the_model_what_order_to_work_in(self):
        message = build_new_request("a phone stand")
        assert "adds a shape" in message
        assert "a phone stand" in message
