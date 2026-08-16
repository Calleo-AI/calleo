"""
Unit tests for faithfulness_scorer.py

The scorer must only audit genuine, knowledge-base-grounded answers. Greetings,
off-topic queries, and canned deferrals retrieve no DB content, so they have
nothing to be faithful/unfaithful *to* and must be skipped (never flagged as
hallucinations). When chunks are present, the judge must also be given the
chatbot's authoritative system-prompt facts so answers drawn from those facts
(tuition, headmaster, founding year, ...) are not flagged.

llm_client.chat is patched so no real LLM call is made; _LOG_PATH is redirected
to a tmp file so no real log is written.
"""
import os
import sys
import json
from unittest.mock import MagicMock

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_analysis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import faithfulness_scorer


_FAITHFUL_JSON = '{"faithful": true, "score": 1.0, "suspicious_claims": []}'


# ---------------------------------------------------------------------------
# _has_retrieved_content — pure predicate
# ---------------------------------------------------------------------------

class TestHasRetrievedContent:
    def test_empty_list_is_false(self):
        assert faithfulness_scorer._has_retrieved_content([]) is False

    def test_blank_strings_are_false(self):
        assert faithfulness_scorer._has_retrieved_content(["", "   ", "\n"]) is False

    def test_real_chunk_is_true(self):
        assert faithfulness_scorer._has_retrieved_content(["actual passage text"]) is True

    def test_mixed_blank_and_real_is_true(self):
        assert faithfulness_scorer._has_retrieved_content(["", "real text"]) is True


# ---------------------------------------------------------------------------
# score_faithfulness_async — gating behaviour
# ---------------------------------------------------------------------------

class TestScoringGate:
    def test_greeting_with_no_chunks_is_skipped(self, monkeypatch, tmp_path):
        mock_chat = MagicMock()
        monkeypatch.setattr(faithfulness_scorer.llm_client, "chat", mock_chat)
        log = tmp_path / "flog.jsonl"
        monkeypatch.setattr(faithfulness_scorer, "_LOG_PATH", str(log))

        faithfulness_scorer.score_faithfulness_async(
            "hello",
            [],
            "Hello! I am the Example School AI Assistant, how can I help you?",
            "cid-greeting",
        )

        mock_chat.assert_not_called()
        assert not log.exists()

    def test_blank_chunks_are_skipped(self, monkeypatch, tmp_path):
        mock_chat = MagicMock()
        monkeypatch.setattr(faithfulness_scorer.llm_client, "chat", mock_chat)
        log = tmp_path / "flog.jsonl"
        monkeypatch.setattr(faithfulness_scorer, "_LOG_PATH", str(log))

        faithfulness_scorer.score_faithfulness_async(
            "anything", ["", "   "], "Some canned response.", "cid-blank"
        )

        mock_chat.assert_not_called()
        assert not log.exists()

    def test_real_answer_with_chunks_is_scored_and_logged(self, monkeypatch, tmp_path):
        mock_chat = MagicMock(return_value=_FAITHFUL_JSON)
        monkeypatch.setattr(faithfulness_scorer.llm_client, "chat", mock_chat)
        log = tmp_path / "flog.jsonl"
        monkeypatch.setattr(faithfulness_scorer, "_LOG_PATH", str(log))

        faithfulness_scorer.score_faithfulness_async(
            "What grades does Example School serve?",
            ["Example School serves grades 3 through 12."],
            "Example School serves grades 3 to 12.",
            "cid-real",
        )

        mock_chat.assert_called_once()
        assert log.exists()
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["faithful"] is True
        assert record["conversation_id"] == "cid-real"


# ---------------------------------------------------------------------------
# trusted_facts — system-prompt knowledge given to the judge
# ---------------------------------------------------------------------------

class TestTrustedFacts:
    def test_trusted_facts_and_chunks_both_in_judge_prompt(self, monkeypatch, tmp_path):
        captured = {}

        def fake_chat(messages, role=None, **kwargs):
            captured["prompt"] = messages[0]["content"]
            return _FAITHFUL_JSON

        monkeypatch.setattr(faithfulness_scorer.llm_client, "chat", fake_chat)
        monkeypatch.setattr(faithfulness_scorer, "_LOG_PATH", str(tmp_path / "f.jsonl"))

        faithfulness_scorer.score_faithfulness_async(
            "Who is the head of school?",
            ["A general chunk about the school."],
            "The head of school is Alex Doe.",
            "cid-facts",
            trusted_facts="The current head of school is Alex Doe, appointed in 2020.",
        )

        prompt = captured["prompt"]
        assert "Alex Doe" in prompt
        assert "A general chunk about the school." in prompt
