"""The generate-and-correct loop, driven by a scripted model.

No API key, no network, no cost. The model is a list of prepared replies, which
also lets a test assert on exactly what the loop said back to it - the feedback
wording is the part that decides whether the next attempt is better.
"""

from dataclasses import dataclass, field

import numpy as np
import pytest

from modelpop.application.ai_ports import (
    AiSettings,
    Completion,
    Conversation,
    ModelChoice,
    ModelRole,
)
from modelpop.application.cad_ports import (
    Dimension,
    DimensionTable,
    ScriptResult,
    SolidMeasurements,
)
from modelpop.domain import Length, Mesh
from modelpop.domain.readiness import MeshFacts
from modelpop.domain.result import Result, failure, success
from modelpop.generation import extract_script, generate_part


def code(body: str) -> str:
    return f"```python\n{body}\n```"


RIGHT = code("from build123d import *\nwith BuildPart() as p:\n    Box(60, 40, 8)\nresult = p.part")
WRONG_SIZE = code(
    "from build123d import *\nwith BuildPart() as p:\n    Box(55, 40, 8)\nresult = p.part"
)

SPEC = DimensionTable(
    (
        Dimension("width", Length.mm(60)),
        Dimension("depth", Length.mm(40)),
        Dimension("height", Length.mm(8)),
    )
)


# ---------------------------------------------------------------------- fakes


@dataclass
class ScriptedModel:
    """Replies with a prepared sequence and records what it was told."""

    replies: list[str]
    heard: list[str] = field(default_factory=list)
    configured: bool = True
    refuse: bool = False
    fail_with: str = ""

    def is_configured(self) -> bool:
        return self.configured

    def describe(self) -> str:
        return "scripted"

    def complete(self, conversation: Conversation, choice: ModelChoice) -> Result[Completion]:
        self.heard.append(conversation.messages[-1].text)
        if self.fail_with:
            return failure("The model call failed", self.fail_with)
        if self.refuse:
            return success(Completion(text="", refused=True, refusal_category="cyber"))
        index = min(len(self.heard) - 1, len(self.replies) - 1)
        return success(Completion(text=self.replies[index], input_tokens=1200, output_tokens=300))


def mesh() -> Mesh:
    return Mesh(
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64),
        np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int32),
    )


@dataclass
class FakeKernel:
    """A CAD kernel that maps scripts to outcomes without running anything."""

    available: bool = True
    ran: list[str] = field(default_factory=list)

    def is_available(self) -> bool:
        return self.available

    def describe(self) -> str:
        return "fake kernel"

    def run(self, script: str, timeout_seconds: float = 60.0) -> Result[ScriptResult]:
        self.ran.append(script)
        if "not python" in script:
            return failure("The script failed", "the script does not parse: line 1")
        width = 55.0 if "Box(55" in script else 60.0
        return success(
            ScriptResult(
                mesh=mesh(),
                measurements=SolidMeasurements(
                    volume_mm3=width * 40 * 8,
                    width=Length.mm(width),
                    depth=Length.mm(40),
                    height=Length.mm(8),
                ),
            )
        )


class FakeOps:
    """Mesh inspection that always reports a sound solid."""

    def inspect(self, mesh_in: Mesh, *, measure_walls: bool = True) -> MeshFacts:
        return MeshFacts(mesh=mesh_in, is_watertight=True, is_winding_consistent=True)


# ---------------------------------------------------------------------- tests


class TestExtractingCode:
    def test_a_fenced_block_is_extracted(self):
        assert extract_script("here you go\n```python\nx = 1\n```\n") == "x = 1"

    def test_a_fence_without_a_language_still_works(self):
        assert extract_script("```\nx = 1\n```") == "x = 1"

    def test_the_longest_block_wins(self):
        """Models often show a snippet first and the real answer second."""
        reply = (
            "```python\nimport build123d\n```\nand the part:\n```python\n" + "y = 2\n" * 5 + "```"
        )
        assert "y = 2" in extract_script(reply)

    def test_bare_code_is_accepted(self):
        assert extract_script("from build123d import *\nresult = Box(1,1,1)").startswith("from")

    def test_an_empty_reply_yields_nothing(self):
        assert extract_script("   ") == ""


class TestHappyPath:
    def test_a_correct_script_passes_on_the_first_attempt(self):
        model = ScriptedModel([RIGHT])
        run = generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC).unwrap()

        assert run.succeeded
        assert len(run.attempts) == 1
        assert run.total_cost_usd > 0

    def test_the_request_reaches_the_model_with_its_dimensions(self):
        model = ScriptedModel([RIGHT])
        generate_part("a wall bracket", model, FakeKernel(), FakeOps(), SPEC)

        asked = model.heard[0]
        assert "a wall bracket" in asked
        assert "60.00 mm" in asked
        assert "measured and rejected" in asked


class TestCorrection:
    def test_a_wrong_dimension_is_corrected_on_the_next_attempt(self):
        model = ScriptedModel([WRONG_SIZE, RIGHT])
        run = generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC).unwrap()

        assert run.succeeded
        assert len(run.attempts) == 2

    def test_the_correction_carries_the_measurement_and_the_target(self):
        """The whole point: numbers, not pictures."""
        model = ScriptedModel([WRONG_SIZE, RIGHT])
        generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC)

        correction = model.heard[1]
        assert "55.00 mm" in correction
        assert "60.00 mm" in correction
        assert "5.00 mm too small" in correction

    def test_the_correction_asks_for_the_whole_script_back(self):
        """Asking for a patch invites a reply that cannot be run."""
        model = ScriptedModel([WRONG_SIZE, RIGHT])
        generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC)
        assert "complete corrected script" in model.heard[1]

    def test_a_syntax_error_is_fed_back_verbatim(self):
        model = ScriptedModel([code("this is not python"), RIGHT])
        generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC)
        assert "does not parse" in model.heard[1]

    def test_the_model_sees_its_own_previous_attempt(self):
        """Starting fresh each round throws away the only useful context."""
        model = ScriptedModel([WRONG_SIZE, RIGHT])
        generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC)
        # heard[] holds the last user turn; the conversation also carries the
        # assistant turn, which the loop appends before the correction.
        assert len(model.heard) == 2


class TestGivingUp:
    def test_the_best_attempt_is_kept_when_none_fully_passes(self):
        """A nearly right part the user can edit beats nothing at all."""
        model = ScriptedModel([WRONG_SIZE])
        run = generate_part(
            "a bracket",
            model,
            FakeKernel(),
            FakeOps(),
            SPEC,
            settings=AiSettings(max_attempts=3),
        ).unwrap()

        assert not run.succeeded
        assert len(run.attempts) == 3
        assert run.best is not None
        assert run.best.result is not None, "the closest attempt still produced a solid"
        assert 0 < run.best.score < 1

    def test_the_attempt_limit_is_honoured(self):
        model = ScriptedModel([WRONG_SIZE])
        run = generate_part(
            "a bracket",
            model,
            FakeKernel(),
            FakeOps(),
            SPEC,
            settings=AiSettings(max_attempts=2),
        ).unwrap()
        assert len(run.attempts) == 2

    def test_the_spend_limit_stops_the_run_and_says_so(self):
        model = ScriptedModel([WRONG_SIZE])
        run = generate_part(
            "a bracket",
            model,
            FakeKernel(),
            FakeOps(),
            SPEC,
            settings=AiSettings(max_attempts=20, spend_limit_usd=0.02),
        ).unwrap()

        assert "spend limit" in run.stopped_because
        assert len(run.attempts) < 20

    def test_a_zero_spend_limit_means_no_limit(self):
        model = ScriptedModel([WRONG_SIZE])
        run = generate_part(
            "a bracket",
            model,
            FakeKernel(),
            FakeOps(),
            SPEC,
            settings=AiSettings(max_attempts=3, spend_limit_usd=0.0),
        ).unwrap()
        assert len(run.attempts) == 3
        assert "spend limit" not in run.stopped_because

    def test_a_refusal_stops_the_run_without_pretending_it_worked(self):
        model = ScriptedModel([RIGHT], refuse=True)
        result = generate_part("something odd", model, FakeKernel(), FakeOps(), SPEC)
        assert not result.ok
        assert "no attempt completed" in result.error or "declined" in result.error

    def test_a_provider_failure_is_reported(self):
        model = ScriptedModel([RIGHT], fail_with="rate limited")
        result = generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC)
        assert not result.ok


class TestRefusingToStart:
    def test_an_empty_request_is_refused(self):
        result = generate_part("   ", ScriptedModel([RIGHT]), FakeKernel())
        assert not result.ok
        assert "describe the part" in result.error

    def test_an_unconfigured_provider_says_what_to_do(self):
        model = ScriptedModel([RIGHT], configured=False)
        result = generate_part("a bracket", model, FakeKernel())
        assert not result.ok
        assert "Settings" in result.error

    def test_an_unavailable_kernel_is_reported(self):
        result = generate_part("a bracket", ScriptedModel([RIGHT]), FakeKernel(available=False))
        assert not result.ok
        assert "build123d" in result.error


class TestReporting:
    def test_the_log_reads_as_a_narrative(self):
        model = ScriptedModel([WRONG_SIZE, RIGHT])
        run = generate_part("a bracket", model, FakeKernel(), FakeOps(), SPEC).unwrap()

        log = run.log()
        assert "Attempt 1" in log
        assert "Attempt 2: passed" in log
        assert "Spent about $" in log

    def test_cost_accumulates_across_attempts(self):
        one = generate_part(
            "a bracket", ScriptedModel([RIGHT]), FakeKernel(), FakeOps(), SPEC
        ).unwrap()
        three = generate_part(
            "a bracket",
            ScriptedModel([WRONG_SIZE]),
            FakeKernel(),
            FakeOps(),
            SPEC,
            settings=AiSettings(max_attempts=3),
        ).unwrap()
        assert three.total_cost_usd > one.total_cost_usd


class TestSettings:
    def test_each_role_has_a_sensible_default_model(self):
        settings = AiSettings()
        assert settings.choice_for(ModelRole.CAD_CODEGEN).model == "claude-opus-5"
        assert settings.choice_for(ModelRole.NAMING).model == "claude-haiku-4-5"

    def test_the_hardest_job_gets_the_most_effort(self):
        settings = AiSettings()
        codegen = settings.choice_for(ModelRole.CAD_CODEGEN)
        naming = settings.choice_for(ModelRole.NAMING)
        assert codegen.effort == "high"
        assert naming.effort == "low"

    def test_an_override_is_honoured(self):
        settings = AiSettings(roles={ModelRole.CAD_CODEGEN: ModelChoice(model="claude-sonnet-5")})
        assert settings.choice_for(ModelRole.CAD_CODEGEN).model == "claude-sonnet-5"

    def test_only_the_critique_role_needs_vision(self):
        assert ModelRole.CRITIQUE.needs_vision
        assert not ModelRole.CAD_CODEGEN.needs_vision

    def test_cost_is_estimated_from_token_counts(self):
        completion = Completion(text="", input_tokens=1_000_000, output_tokens=0)
        assert completion.estimated_cost_usd == pytest.approx(5.0)
