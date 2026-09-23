"""The Anthropic adapter and the secret store.

Driven against a stand-in SDK, so the suite is deterministic, free, and runs
with no key present. The adversarial cases matter most: a refusal arrives as a
successful HTTP 200, and reading its content as an answer produces a baffling
failure three stages later.
"""

import base64
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from modelpop.ai import ANTHROPIC_KEY_NAME, AnthropicProvider, EnvironmentSecretStore
from modelpop.ai.secrets import LayeredSecretStore
from modelpop.application.ai_ports import Conversation, Message, ModelChoice, Role

# ---------------------------------------------------------------------- fakes


@dataclass
class FakeStore:
    """A secret store backed by a dict."""

    values: dict[str, str] = field(default_factory=dict)

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)

    def describe(self):
        return "a test store"


def response(
    text: str = "hello",
    stop_reason: str = "end_turn",
    category: str | None = None,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cached: int = 0,
):
    """A stand-in for an SDK response object."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        model="claude-opus-5",
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cached,
        ),
    )


@dataclass
class FakeClient:
    """Captures the request and returns a prepared response."""

    reply: object = None
    raises: Exception | None = None
    requests: list[dict] = field(default_factory=list)

    def __post_init__(self):
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                if outer.raises is not None:
                    raise outer.raises
                return outer.reply if outer.reply is not None else response()

        self.messages = Messages()


def provider_with(client: FakeClient, key: str = "sk-ant-test") -> AnthropicProvider:
    made = AnthropicProvider(FakeStore({ANTHROPIC_KEY_NAME: key} if key else {}))
    made._client = client
    return made


CONVERSATION = Conversation(system="be helpful", messages=(Message.user("hello"),))
CHOICE = ModelChoice(model="claude-opus-5", effort="high", max_tokens=1000)


# ------------------------------------------------------------- configuration


class TestConfiguration:
    def test_it_reports_when_no_key_is_present(self):
        assert not AnthropicProvider(FakeStore()).is_configured()

    def test_it_reports_when_a_key_is_present(self):
        assert AnthropicProvider(FakeStore({ANTHROPIC_KEY_NAME: "sk-ant-x"})).is_configured()

    def test_the_description_says_when_a_key_is_missing(self):
        assert "no API key" in AnthropicProvider(FakeStore()).describe()

    def test_it_never_reveals_the_key_in_its_description(self):
        described = AnthropicProvider(FakeStore({ANTHROPIC_KEY_NAME: "sk-ant-secret"})).describe()
        assert "secret" not in described

    def test_calling_without_a_key_says_what_to_do(self):
        result = AnthropicProvider(FakeStore()).complete(CONVERSATION, CHOICE)
        assert not result.ok
        assert "Settings" in result.error

    def test_it_supports_vision(self):
        assert AnthropicProvider(FakeStore()).supports_vision()


# ------------------------------------------------------------------ requests


class TestRequestShape:
    def test_the_model_and_token_limit_are_sent(self):
        client = FakeClient()
        provider_with(client).complete(CONVERSATION, CHOICE)
        sent = client.requests[0]
        assert sent["model"] == "claude-opus-5"
        assert sent["max_tokens"] == 1000

    def test_thinking_is_adaptive_and_depth_comes_from_effort(self):
        """`budget_tokens` is rejected on current models - do not reintroduce it."""
        client = FakeClient()
        provider_with(client).complete(CONVERSATION, CHOICE)
        sent = client.requests[0]

        assert sent["thinking"] == {"type": "adaptive"}
        assert sent["output_config"]["effort"] == "high"
        assert "budget_tokens" not in str(sent)

    def test_an_older_model_is_sent_no_thinking_configuration(self):
        client = FakeClient()
        provider_with(client).complete(CONVERSATION, ModelChoice(model="claude-3-5-haiku"))
        assert "thinking" not in client.requests[0]

    def test_the_system_prompt_is_marked_cacheable(self):
        """It is identical on every attempt of a correction loop."""
        client = FakeClient()
        provider_with(client).complete(CONVERSATION, CHOICE)
        system = client.requests[0]["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_no_system_block_is_sent_when_there_is_no_system_prompt(self):
        client = FakeClient()
        provider_with(client).complete(Conversation(messages=(Message.user("hi"),)), CHOICE)
        assert "system" not in client.requests[0]

    def test_a_multi_turn_conversation_is_sent_in_order(self):
        client = FakeClient()
        conversation = Conversation(
            messages=(Message.user("one"), Message.assistant("two"), Message.user("three"))
        )
        provider_with(client).complete(conversation, CHOICE)
        roles = [m["role"] for m in client.requests[0]["messages"]]
        assert roles == ["user", "assistant", "user"]


class TestImages:
    def test_an_image_is_sent_as_base64(self):
        client = FakeClient()
        conversation = Conversation(
            messages=(Message.with_image("what is this?", "image/png", b"\x89PNG-data"),)
        )
        provider_with(client).complete(conversation, CHOICE)

        blocks = client.requests[0]["messages"][0]["content"]
        image = next(b for b in blocks if b["type"] == "image")
        assert image["source"]["media_type"] == "image/png"
        assert base64.standard_b64decode(image["source"]["data"]) == b"\x89PNG-data"

    def test_the_image_comes_before_the_question(self):
        """The question should follow what it is about."""
        client = FakeClient()
        conversation = Conversation(
            messages=(Message.with_image("what is this?", "image/png", b"x"),)
        )
        provider_with(client).complete(conversation, CHOICE)
        blocks = client.requests[0]["messages"][0]["content"]
        assert blocks[0]["type"] == "image"
        assert blocks[-1]["type"] == "text"

    def test_the_base64_carries_no_newlines(self):
        client = FakeClient()
        conversation = Conversation(messages=(Message.with_image("x", "image/png", b"y" * 500),))
        provider_with(client).complete(conversation, CHOICE)
        data = client.requests[0]["messages"][0]["content"][0]["source"]["data"]
        assert "\n" not in data


# ------------------------------------------------------------------ responses


class TestReadingResponses:
    def test_text_is_extracted(self):
        result = provider_with(FakeClient(reply=response("the answer"))).complete(
            CONVERSATION, CHOICE
        )
        assert result.unwrap().text == "the answer"

    def test_usage_is_reported_so_a_run_can_show_its_cost(self):
        reply = response(input_tokens=5000, output_tokens=800, cached=4000)
        completion = provider_with(FakeClient(reply=reply)).complete(CONVERSATION, CHOICE).unwrap()

        assert completion.input_tokens == 5000
        assert completion.output_tokens == 800
        assert completion.cached_tokens == 4000
        assert completion.estimated_cost_usd > 0

    def test_non_text_blocks_are_ignored(self):
        reply = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="hmm"),
                SimpleNamespace(type="text", text="the answer"),
            ],
            stop_reason="end_turn",
            stop_details=None,
            model="claude-opus-5",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0),
        )
        assert (
            provider_with(FakeClient(reply=reply)).complete(CONVERSATION, CHOICE).unwrap().text
            == "the answer"
        )

    def test_a_refusal_is_flagged_rather_than_read_as_an_answer(self):
        """A refusal is a successful HTTP 200 with an unusable body."""
        reply = response(text="I can't help with that", stop_reason="refusal", category="cyber")
        completion = provider_with(FakeClient(reply=reply)).complete(CONVERSATION, CHOICE).unwrap()

        assert completion.refused
        assert completion.refusal_category == "cyber"

    def test_an_ordinary_answer_is_not_flagged_as_refused(self):
        assert not provider_with(FakeClient()).complete(CONVERSATION, CHOICE).unwrap().refused

    def test_a_missing_stop_details_does_not_break_reading(self):
        reply = response(stop_reason="refusal", category=None)
        completion = provider_with(FakeClient(reply=reply)).complete(CONVERSATION, CHOICE).unwrap()
        assert completion.refused
        assert completion.refusal_category == ""


class TestErrors:
    @pytest.mark.parametrize(
        ("exception_name", "expected"),
        [
            ("AuthenticationError", "rejected"),
            ("RateLimitError", "Rate limited"),
            ("NotFoundError", "not available"),
            ("APIConnectionError", "Could not reach"),
            ("APITimeoutError", "timed out"),
            ("PermissionDeniedError", "permission"),
        ],
    )
    def test_each_failure_is_explained_in_terms_the_user_can_act_on(self, exception_name, expected):
        raised = type(exception_name, (Exception,), {})("boom")
        result = provider_with(FakeClient(raises=raised)).complete(CONVERSATION, CHOICE)

        assert not result.ok
        assert expected.lower() in result.error.lower()

    def test_a_server_error_is_recognised_by_status_code(self):
        raised = type("MysteryError", (Exception,), {})("boom")
        raised.status_code = 503
        result = provider_with(FakeClient(raises=raised)).complete(CONVERSATION, CHOICE)
        assert "server error" in result.error.lower()

    def test_an_unknown_failure_still_names_itself(self):
        raised = type("WeirdError", (Exception,), {})("something odd")
        result = provider_with(FakeClient(raises=raised)).complete(CONVERSATION, CHOICE)
        assert not result.ok
        assert "WeirdError" in result.error

    def test_no_failure_ever_escapes_as_an_exception(self):
        for raised in (ValueError("x"), RuntimeError("y"), OSError("z")):
            result = provider_with(FakeClient(raises=raised)).complete(CONVERSATION, CHOICE)
            assert not result.ok


# -------------------------------------------------------------------- secrets


class TestSecretStore:
    def test_the_environment_is_read(self, monkeypatch):
        monkeypatch.setenv("MODELPOP_TEST_KEY", "from-env")
        assert EnvironmentSecretStore().get("MODELPOP_TEST_KEY") == "from-env"

    def test_an_unset_variable_reads_as_none(self):
        assert EnvironmentSecretStore().get("MODELPOP_DEFINITELY_UNSET") is None

    def test_an_empty_variable_reads_as_none(self, monkeypatch):
        """An exported-but-blank key is not a key."""
        monkeypatch.setenv("MODELPOP_TEST_KEY", "")
        assert EnvironmentSecretStore().get("MODELPOP_TEST_KEY") is None

    def test_writing_to_the_environment_is_refused_rather_than_silently_dropped(self):
        with pytest.raises(RuntimeError, match="cannot be saved"):
            EnvironmentSecretStore().set("MODELPOP_TEST_KEY", "x")

    def test_the_environment_wins_over_the_stored_key(self, monkeypatch):
        """What a developer exports in their shell is what they expect to be used."""
        monkeypatch.setenv("MODELPOP_TEST_KEY", "from-env")
        layered = LayeredSecretStore(FakeStore({"MODELPOP_TEST_KEY": "from-store"}))
        assert layered.get("MODELPOP_TEST_KEY") == "from-env"

    def test_the_stored_key_is_used_when_nothing_is_exported(self, monkeypatch):
        monkeypatch.delenv("MODELPOP_TEST_KEY", raising=False)
        layered = LayeredSecretStore(FakeStore({"MODELPOP_TEST_KEY": "from-store"}))
        assert layered.get("MODELPOP_TEST_KEY") == "from-store"

    def test_it_says_which_layer_a_key_came_from(self, monkeypatch):
        """A user with both set needs to know which one is live."""
        monkeypatch.setenv("MODELPOP_TEST_KEY", "from-env")
        layered = LayeredSecretStore(FakeStore({"MODELPOP_TEST_KEY": "from-store"}))
        assert layered.source_of("MODELPOP_TEST_KEY") == "environment variable"

        monkeypatch.delenv("MODELPOP_TEST_KEY")
        assert "test store" in layered.source_of("MODELPOP_TEST_KEY")

    def test_an_absent_key_is_reported_as_not_set(self, monkeypatch):
        monkeypatch.delenv("MODELPOP_TEST_KEY", raising=False)
        assert LayeredSecretStore(FakeStore()).source_of("MODELPOP_TEST_KEY") == "not set"

    def test_writes_go_to_the_credential_store(self):
        store = FakeStore()
        LayeredSecretStore(store).set("MODELPOP_TEST_KEY", "saved")
        assert store.values["MODELPOP_TEST_KEY"] == "saved"

    def test_deleting_something_absent_is_not_an_error(self):
        LayeredSecretStore(FakeStore()).delete("MODELPOP_TEST_KEY")


class TestMessages:
    def test_the_constructors_set_the_right_role(self):
        assert Message.user("x").role is Role.USER
        assert Message.assistant("x").role is Role.ASSISTANT
        assert Message.with_image("x", "image/png", b"y").role is Role.USER

    def test_a_conversation_is_extended_without_being_mutated(self):
        original = Conversation(messages=(Message.user("one"),))
        extended = original.then(Message.assistant("two"))
        assert len(original.messages) == 1
        assert len(extended.messages) == 2
