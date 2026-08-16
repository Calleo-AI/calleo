"""
Unit tests for llm_client.py — the centralized provider seam.

Env vars are stubbed before import so neither the OpenAI (OpenRouter) client
nor the Gemini embedding function attempts real authentication.

The provider client is lazy and constructed via llm_client._get_client(); tests
swap that out for a MagicMock so no network/auth ever happens and we can inspect
exactly what request kwargs chat() builds.
"""
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
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

class TestEmbeddingFunction:
    def test_returns_same_singleton_each_call(self):
        a = llm_client.get_embedding_function()
        b = llm_client.get_embedding_function()
        assert a is b

    def test_is_a_google_embedding_function(self):
        import chromadb.utils.embedding_functions as embedding_functions
        ef = llm_client.get_embedding_function()
        assert isinstance(ef, embedding_functions.GoogleGenerativeAiEmbeddingFunction)
