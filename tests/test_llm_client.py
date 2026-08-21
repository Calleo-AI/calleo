"""
Unit tests for llm_client.py — the centralized provider seam.

Env vars are stubbed before import so the OpenAI (OpenRouter) client never
attempts real authentication.

The provider client is lazy and constructed via llm_client._get_client(); tests
swap that out for a MagicMock so no network/auth ever happens and we can inspect
exactly what request kwargs chat() and the embedding function build. Everything
— chat and embeddings alike — goes through that one client.
"""
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import llm_client


def _completion(content):
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


@pytest.fixture
def fake_client(monkeypatch):
    """Replace the lazy OpenAI client with a MagicMock and return it.

    Inspect fake_client.chat.completions.create.call_args to assert request shape.
    """
    client = MagicMock()
    monkeypatch.setattr(llm_client, "_get_client", lambda: client)
    return client


# ---------------------------------------------------------------------------
# model resolution
# ---------------------------------------------------------------------------

class TestModelFor:
    def test_chat_role_defaults_to_qwen(self):
        assert llm_client.model_for("chat") == "qwen/qwen3.5-397b-a17b"

    def test_analysis_role_defaults_to_deepseek(self):
        assert llm_client.model_for("analysis") == "deepseek/deepseek-v4-flash"

    def test_judge_role_defaults_to_deepseek(self):
        assert llm_client.model_for("judge") == "deepseek/deepseek-v4-flash"

    def test_unknown_role_raises_value_error(self):
        with pytest.raises(ValueError):
            llm_client.model_for("nonexistent")

    def test_env_var_overrides_default_model(self, monkeypatch):
        monkeypatch.setenv("CHAT_MODEL", "google/gemini-2.5-pro")
        assert llm_client.model_for("chat") == "google/gemini-2.5-pro"


# ---------------------------------------------------------------------------
# chat() — return value
# ---------------------------------------------------------------------------

class TestChatReturn:
    def test_returns_message_content(self, fake_client):
        fake_client.chat.completions.create.return_value = _completion("Hello there")
        assert llm_client.chat([{"role": "user", "content": "hi"}]) == "Hello there"

    def test_returns_empty_string_when_content_is_none(self, fake_client):
        fake_client.chat.completions.create.return_value = _completion(None)
        assert llm_client.chat([{"role": "user", "content": "hi"}]) == ""

    def test_does_not_strip_content(self, fake_client):
        # Callers do their own trimming; preserve raw content so existing
        # .strip()/json.loads logic at call sites stays exact.
        fake_client.chat.completions.create.return_value = _completion("  padded  ")
        assert llm_client.chat([{"role": "user", "content": "hi"}]) == "  padded  "

    def test_propagates_exceptions(self, fake_client):
        fake_client.chat.completions.create.side_effect = RuntimeError("upstream down")
        with pytest.raises(RuntimeError):
            llm_client.chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# chat_with_usage() — token accounting
# ---------------------------------------------------------------------------

def _completion_with_usage(content, prompt_tokens, completion_tokens):
    mock = _completion(content)
    mock.usage.prompt_tokens = prompt_tokens
    mock.usage.completion_tokens = completion_tokens
    return mock


class TestChatWithUsage:
    def test_returns_text_and_parsed_counts(self, fake_client):
        fake_client.chat.completions.create.return_value = _completion_with_usage(
            "Hello there", 120, 34)
        text, usage = llm_client.chat_with_usage([{"role": "user", "content": "hi"}])
        assert text == "Hello there"
        assert usage == {"prompt_tokens": 120, "completion_tokens": 34}

    def test_zeros_when_provider_omits_usage(self, fake_client):
        completion = _completion("Hello there")
        completion.usage = None
        fake_client.chat.completions.create.return_value = completion
        _, usage = llm_client.chat_with_usage([{"role": "user", "content": "hi"}])
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0}

    def test_zeros_for_non_integer_counts(self, fake_client):
        # A bare MagicMock auto-creates .usage.prompt_tokens as another MagicMock.
        # These counts are written straight into ChromaDB metadata, which accepts
        # only str/int/float/bool — a non-int here would raise at insert time and
        # lose the whole conversation-log row.
        fake_client.chat.completions.create.return_value = _completion("Hello there")
        _, usage = llm_client.chat_with_usage([{"role": "user", "content": "hi"}])
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0}
        assert all(isinstance(v, int) for v in usage.values())

    def test_empty_content_still_reports_usage(self, fake_client):
        fake_client.chat.completions.create.return_value = _completion_with_usage(
            None, 80, 0)
        text, usage = llm_client.chat_with_usage([{"role": "user", "content": "hi"}])
        assert text == ""
        assert usage["prompt_tokens"] == 80

    def test_propagates_exceptions(self, fake_client):
        fake_client.chat.completions.create.side_effect = RuntimeError("upstream down")
        with pytest.raises(RuntimeError):
            llm_client.chat_with_usage([{"role": "user", "content": "hi"}])

    def test_chat_is_a_thin_wrapper_returning_only_text(self, fake_client):
        fake_client.chat.completions.create.return_value = _completion_with_usage(
            "Hello there", 120, 34)
        assert llm_client.chat([{"role": "user", "content": "hi"}]) == "Hello there"

    def test_both_entry_points_build_the_same_request(self, fake_client):
        fake_client.chat.completions.create.return_value = _completion_with_usage(
            "ok", 1, 1)
        messages = [{"role": "user", "content": "hi"}]
        kwargs = dict(role="analysis", temperature=0, reasoning="high")

        llm_client.chat(messages, **kwargs)
        via_chat = fake_client.chat.completions.create.call_args
        llm_client.chat_with_usage(messages, **kwargs)
        via_usage = fake_client.chat.completions.create.call_args

        assert via_chat == via_usage


# ---------------------------------------------------------------------------
# chat() — request construction
# ---------------------------------------------------------------------------

class TestChatRequest:
    def _kwargs(self, fake_client, **call):
        fake_client.chat.completions.create.return_value = _completion("ok")
        llm_client.chat([{"role": "user", "content": "hi"}], **call)
        return fake_client.chat.completions.create.call_args.kwargs

    def test_messages_passed_through(self, fake_client):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        fake_client.chat.completions.create.return_value = _completion("ok")
        llm_client.chat(msgs)
        assert fake_client.chat.completions.create.call_args.kwargs["messages"] == msgs

    def test_chat_role_sends_qwen_model(self, fake_client):
        assert self._kwargs(fake_client, role="chat")["model"] == "qwen/qwen3.5-397b-a17b"

    def test_analysis_role_sends_deepseek_model(self, fake_client):
        assert self._kwargs(fake_client, role="analysis")["model"] == "deepseek/deepseek-v4-flash"

    def test_judge_role_sends_deepseek_model(self, fake_client):
        assert self._kwargs(fake_client, role="judge")["model"] == "deepseek/deepseek-v4-flash"

    def test_default_role_is_chat(self, fake_client):
        assert self._kwargs(fake_client)["model"] == "qwen/qwen3.5-397b-a17b"

    def test_temperature_none_is_omitted(self, fake_client):
        assert "temperature" not in self._kwargs(fake_client, temperature=None)

    def test_temperature_zero_is_sent(self, fake_client):
        # 0 is a meaningful value (deterministic) — must not be dropped as falsy.
        assert self._kwargs(fake_client, temperature=0)["temperature"] == 0

    def test_temperature_nonzero_is_sent(self, fake_client):
        assert self._kwargs(fake_client, temperature=0.3)["temperature"] == 0.3

    def test_reasoning_none_omits_extra_body(self, fake_client):
        # The faithfulness judge sends no reasoning field at all — preserve that.
        assert "extra_body" not in self._kwargs(fake_client, reasoning=None)

    def test_reasoning_off_disables_reasoning(self, fake_client):
        assert self._kwargs(fake_client, reasoning="off")["extra_body"] == {
            "reasoning": {"enabled": False}
        }

    def test_reasoning_high_sets_high_effort(self, fake_client):
        assert self._kwargs(fake_client, reasoning="high")["extra_body"] == {
            "reasoning": {"effort": "high"}
        }

    def test_invalid_reasoning_raises_value_error(self, fake_client):
        fake_client.chat.completions.create.return_value = _completion("ok")
        with pytest.raises(ValueError):
            llm_client.chat([{"role": "user", "content": "hi"}], reasoning="medium-ish")


# ---------------------------------------------------------------------------
# embeddings
# ---------------------------------------------------------------------------

class TestEmbedModel:
    def test_defaults_to_gemini_via_openrouter(self):
        assert llm_client.embed_model() == "google/gemini-embedding-001"

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("EMBED_MODEL", "openai/text-embedding-3-small")
        assert llm_client.embed_model() == "openai/text-embedding-3-small"


def _embedding_response(vectors, start_index=0):
    """Build a provider embeddings response carrying `vectors`."""
    response = MagicMock()
    response.data = [
        MagicMock(index=start_index + i, embedding=v) for i, v in enumerate(vectors)
    ]
    return response


class TestEmbeddingFunction:
    def test_returns_same_singleton_each_call(self):
        a = llm_client.get_embedding_function()
        b = llm_client.get_embedding_function()
        assert a is b

    def test_is_a_chroma_embedding_function(self):
        from chromadb.api.types import EmbeddingFunction

        assert isinstance(llm_client.get_embedding_function(), EmbeddingFunction)

    def test_is_registered_with_chroma_so_configs_deserialize(self):
        # ChromaDB rebuilds the embedding function from the name it persisted in
        # the collection config; an unregistered name raises on collection load.
        from chromadb.utils.embedding_functions import known_embedding_functions

        assert (
            known_embedding_functions["openrouter"]
            is llm_client.OpenRouterEmbeddingFunction
        )

    def test_default_space_is_cosine(self):
        # The collections were built under cosine; changing it would silently
        # re-rank every retrieval.
        assert llm_client.get_embedding_function().default_space() == "cosine"

    def test_uses_the_embed_model(self, monkeypatch):
        monkeypatch.setenv("EMBED_MODEL", "openai/text-embedding-3-small")
        ef = llm_client.OpenRouterEmbeddingFunction()
        assert ef.model_name == "openai/text-embedding-3-small"

    def test_config_round_trips(self):
        ef = llm_client.OpenRouterEmbeddingFunction(model_name="some/model")
        rebuilt = llm_client.OpenRouterEmbeddingFunction.build_from_config(
            ef.get_config()
        )
        assert rebuilt.model_name == "some/model"


class TestEmbeddingRequests:
    def test_sends_texts_to_the_embeddings_endpoint(self, fake_client):
        fake_client.embeddings.create.return_value = _embedding_response(
            [[1.0, 2.0], [3.0, 4.0]]
        )
        ef = llm_client.OpenRouterEmbeddingFunction()
        result = ef(["one", "two"])

        kwargs = fake_client.embeddings.create.call_args.kwargs
        assert kwargs["model"] == "google/gemini-embedding-001"
        assert kwargs["input"] == ["one", "two"]
        assert [list(v) for v in result] == [[1.0, 2.0], [3.0, 4.0]]

    def test_accepts_a_bare_string(self, fake_client):
        fake_client.embeddings.create.return_value = _embedding_response([[1.0, 2.0]])
        result = llm_client.OpenRouterEmbeddingFunction()("just one")
        assert fake_client.embeddings.create.call_args.kwargs["input"] == ["just one"]
        assert len(result) == 1

    def test_reorders_results_by_index(self, fake_client):
        # One vector per input, in input order — whatever order the provider
        # streamed them back in.
        response = MagicMock()
        response.data = [
            MagicMock(index=1, embedding=[3.0, 4.0]),
            MagicMock(index=0, embedding=[1.0, 2.0]),
        ]
        fake_client.embeddings.create.return_value = response
        result = llm_client.OpenRouterEmbeddingFunction()(["one", "two"])
        assert [list(v) for v in result] == [[1.0, 2.0], [3.0, 4.0]]

    def test_splits_large_inputs_into_batches(self, fake_client):
        texts = [f"doc {i}" for i in range(llm_client.EMBED_BATCH_SIZE + 5)]
        fake_client.embeddings.create.side_effect = lambda model, input: (
            _embedding_response([[float(len(t))] for t in input])
        )
        result = llm_client.OpenRouterEmbeddingFunction()(texts)

        assert fake_client.embeddings.create.call_count == 2
        sent = [
            call.kwargs["input"] for call in fake_client.embeddings.create.call_args_list
        ]
        assert sent[0] == texts[:llm_client.EMBED_BATCH_SIZE]
        assert sent[1] == texts[llm_client.EMBED_BATCH_SIZE:]
        assert len(result) == len(texts)

    def test_propagates_provider_errors(self, fake_client):
        # Transient failures are retried inside the SDK client (max_retries);
        # whatever survives that is the caller's problem, as with chat().
        fake_client.embeddings.create.side_effect = RuntimeError("upstream down")
        with pytest.raises(RuntimeError):
            llm_client.OpenRouterEmbeddingFunction()(["one"])


class TestProviderClient:
    def test_configures_openrouter_and_retries(self, monkeypatch):
        # The one place the provider is wired up: base URL, key, retry policy.
        constructed = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                constructed.update(kwargs)

        monkeypatch.setattr(llm_client, "_client", None)
        monkeypatch.setenv("OPENROUTER_API_KEY", "key-123")
        fake_module = MagicMock()
        fake_module.OpenAI = FakeOpenAI
        monkeypatch.setitem(sys.modules, "openai", fake_module)

        llm_client._get_client()
        assert constructed == {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "key-123",
            "max_retries": llm_client.MAX_RETRIES,
        }
