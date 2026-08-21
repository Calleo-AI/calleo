"""
Unit tests for generate_title() and _fallback_title() in chatbot.py.

Env vars are stubbed before import so the lazy provider client in llm_client
does not attempt real authentication against OpenRouter. The LLM call is the
single seam llm_client.chat, which returns the message text directly.
"""
import os
import sys
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_chatbot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import llm_client  # noqa: F401  (ensures the patch target module is importable)


class TestFallbackTitle:
    def test_short_message_returned_verbatim(self):
        from chatbot import _fallback_title
        assert _fallback_title("tuition fees?") == "tuition fees?"

    def test_long_message_truncated_with_ellipsis(self):
        from chatbot import _fallback_title
        msg = "a" * 100
        result = _fallback_title(msg)
        assert len(result) == 36  # 35 chars + ellipsis char
        assert result.endswith("…")

    def test_newlines_collapsed_to_spaces(self):
        from chatbot import _fallback_title
        assert _fallback_title("line1\nline2") == "line1 line2"

    def test_whitespace_stripped(self):
        from chatbot import _fallback_title
        assert _fallback_title("   hello   ") == "hello"


class TestGenerateTitle:
    def test_returns_trimmed_title_on_success(self):
        from chatbot import generate_title
        with patch("llm_client.chat", return_value="  Tuition fees  "):
            assert generate_title("How much is tuition?", "English") == "Tuition fees"

    def test_strips_surrounding_quotes(self):
        from chatbot import generate_title
        with patch("llm_client.chat", return_value='"Tuition fees"'):
            assert generate_title("How much is tuition?", "English") == "Tuition fees"

    def test_falls_back_when_llm_returns_empty(self):
        from chatbot import generate_title
        with patch("llm_client.chat", return_value=""):
            assert generate_title("tuition fees?", "English") == "tuition fees?"

    def test_falls_back_when_llm_returns_over_80_chars(self):
        from chatbot import generate_title
        long = "x" * 120
        with patch("llm_client.chat", return_value=long):
            # Fallback truncation of input message (not the LLM output)
            result = generate_title("short input", "English")
            assert result == "short input"

    def test_passes_language_into_prompt(self):
        from chatbot import generate_title
        with patch("llm_client.chat", return_value="Titre") as mocked:
            generate_title("Combien coûtent les frais?", "French")
            prompt_arg = mocked.call_args.args[0][0]["content"]
            assert "French" in prompt_arg

    def test_raises_from_chatbot_are_handled_by_caller(self):
        # generate_title itself propagates exceptions — server layer handles them.
        from chatbot import generate_title
        with patch("llm_client.chat", side_effect=RuntimeError("upstream down")):
            with pytest.raises(RuntimeError):
                generate_title("anything", "English")

    def test_collapses_internal_whitespace(self):
        from chatbot import generate_title
        with patch("llm_client.chat", return_value="Tuition\n\tfees  for  grade 9"):
            assert generate_title("anything", "English") == "Tuition fees for grade 9"
