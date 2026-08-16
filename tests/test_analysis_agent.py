"""
Unit tests for analysis_agent.py

parse_conversations is pure string logic with no external dependencies, so it
is tested directly. analyze_with_openrouter is tested for its empty-input
short-circuit only; real LLM calls are not made.
"""
import os
import sys
import json

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_analysis"))

import pytest
import analysis_agent
from analysis_agent import parse_conversations


# ---------------------------------------------------------------------------
# parse_conversations
# ---------------------------------------------------------------------------

class TestParseConversations:
    def test_parses_standard_format(self):
        docs = ["User: What are the fees?\nAI: Tuition is $20,000 per year."]
        result = parse_conversations(docs)
        assert len(result) == 1
        assert result[0]["query"] == "What are the fees?"
        assert result[0]["response"] == "Tuition is $20,000 per year."

    def test_parses_multiple_documents(self):
        docs = [
            "User: Question one?\nAI: Answer one.",
            "User: Question two?\nAI: Answer two.",
        ]
        result = parse_conversations(docs)
        assert len(result) == 2
        assert result[0]["query"] == "Question one?"
        assert result[1]["query"] == "Question two?"

    def test_skips_documents_without_ai_separator(self):
        docs = [
            "This has no AI separator at all",
            "User: Valid question?\nAI: Valid answer.",
        ]
        result = parse_conversations(docs)
        assert len(result) == 1
        assert result[0]["query"] == "Valid question?"

    def test_empty_list_returns_empty_list(self):
        assert parse_conversations([]) == []

    def test_all_malformed_documents_returns_empty_list(self):
        docs = ["no separator", "also no separator here"]
        assert parse_conversations(docs) == []

    def test_strips_whitespace_from_query_and_response(self):
        docs = ["User:   padded query   \nAI:   padded response   "]
        result = parse_conversations(docs)
        assert result[0]["query"] == "padded query"
        assert result[0]["response"] == "padded response"

    def test_multiline_ai_response_is_preserved(self):
        # Only the first "\nAI: " is used as the split point, so newlines
        # within the AI response are kept intact.
        docs = ["User: Tell me about admissions.\nAI: Step 1.\nStep 2.\nStep 3."]
        result = parse_conversations(docs)
        assert "Step 1." in result[0]["response"]
        assert "Step 2." in result[0]["response"]

    def test_each_entry_has_query_and_response_keys(self):
        docs = ["User: question?\nAI: answer."]
        result = parse_conversations(docs)
        assert "query" in result[0]
        assert "response" in result[0]

    def test_user_prefix_stripped_from_query(self):
        docs = ["User: My question\nAI: My answer"]
        result = parse_conversations(docs)
        assert not result[0]["query"].startswith("User:")

    def test_document_with_only_whitespace_after_split_is_handled(self):
        # The AI part is empty — should still parse without crashing
        docs = ["User: question\nAI: "]
        result = parse_conversations(docs)
        assert len(result) == 1
        assert result[0]["response"] == ""


# ---------------------------------------------------------------------------
# analyze_with_openrouter — empty-input guard
# ---------------------------------------------------------------------------

class TestAnalyzeWithOpenRouter:
    def test_empty_conversations_returns_no_conversations_message(self):
        result = analysis_agent.analyze_with_openrouter([])
        assert result == "No conversations found to analyze."


# ---------------------------------------------------------------------------
# build_hallucination_section — suspicious claims are only surfaced when the
# faithfulness score is very low (<= SUSPICIOUS_CLAIM_SCORE_THRESHOLD). Records
# flagged unfaithful but scoring above the cutoff are omitted from the detail
# list entirely, while the top-line summary still counts every flagged record.
# ---------------------------------------------------------------------------

def _write_faithfulness_log(tmp_path, records):
    log = tmp_path / "faithfulness_log.jsonl"
    with open(log, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(log)


def _faithfulness_record(score, faithful, claims, query="Some query",
                         ts="2026-06-16T10:00:00"):
    return {
        "timestamp": ts,
        "conversation_id": "cid",
        "query": query,
        "score": score,
        "faithful": faithful,
        "suspicious_claims": claims,
        "response_snippet": "snippet",
    }


class TestBuildHallucinationSection:
    def test_returns_none_when_log_missing(self, tmp_path):
        missing = str(tmp_path / "does_not_exist.jsonl")
        assert analysis_agent.build_hallucination_section(log_path=missing) is None

    def test_claims_shown_when_score_below_threshold(self, tmp_path):
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(0.1, False, ["Tuition is exactly $5"],
                                 query="How much is tuition?"),
        ])
        section = analysis_agent.build_hallucination_section(log_path=log)
        assert "Suspicious claim: Tuition is exactly $5" in section
        assert "How much is tuition?" in section

    def test_claims_and_entry_hidden_when_score_above_threshold(self, tmp_path):
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(0.6, False, ["Possibly wrong claim"],
                                 query="Borderline question?"),
        ])
        section = analysis_agent.build_hallucination_section(log_path=log)
        assert "Possibly wrong claim" not in section
        assert "Borderline question?" not in section
        # The record is still counted in the top-line summary.
        assert "flagged as unfaithful:** 1" in section

    def test_score_exactly_at_threshold_is_included(self, tmp_path):
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(0.3, False, ["Edge-of-threshold claim"],
                                 query="Edge question?"),
        ])
        section = analysis_agent.build_hallucination_section(log_path=log)
        assert "Edge-of-threshold claim" in section

    def test_summary_counts_all_flagged_but_detail_only_low_scores(self, tmp_path):
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(0.1, False, ["Low-score claim"],
                                 query="Low score query?"),
            _faithfulness_record(0.9, False, ["High-score claim"],
                                 query="High score query?"),
        ])
        section = analysis_agent.build_hallucination_section(log_path=log)
        assert "Low-score claim" in section
        assert "Low score query?" in section
        assert "High-score claim" not in section
        assert "High score query?" not in section
        assert "flagged as unfaithful:** 2" in section

    def test_nonnumeric_score_is_omitted_from_detail(self, tmp_path):
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(None, False, ["Unknown-score claim"],
                                 query="No score query?"),
        ])
        section = analysis_agent.build_hallucination_section(log_path=log)
        assert "Unknown-score claim" not in section
        assert "No score query?" not in section

    def test_faithful_records_never_appear_in_detail(self, tmp_path):
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(1.0, True, [], query="Perfectly fine query?"),
        ])
        section = analysis_agent.build_hallucination_section(log_path=log)
        assert "Perfectly fine query?" not in section

    def test_since_excludes_records_before_the_window(self, tmp_path):
        import datetime
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(0.1, False, ["Old claim"],
                                 query="Old query?", ts="2026-06-01T10:00:00"),
            _faithfulness_record(0.1, False, ["Recent claim"],
                                 query="Recent query?", ts="2026-06-20T10:00:00"),
        ])
        cutoff = datetime.datetime(2026, 6, 15)
        section = analysis_agent.build_hallucination_section(log_path=log, since=cutoff)
        assert "Recent claim" in section
        assert "Old claim" not in section
        # Only the in-window record is counted in the top-line summary.
        assert "ran on **1** chatbot responses" in section

    def test_since_returns_none_when_all_records_predate_window(self, tmp_path):
        import datetime
        log = _write_faithfulness_log(tmp_path, [
            _faithfulness_record(0.1, False, ["Old claim"], ts="2026-06-01T10:00:00"),
        ])
        cutoff = datetime.datetime(2026, 6, 15)
        assert analysis_agent.build_hallucination_section(log_path=log, since=cutoff) is None

    def test_since_excludes_records_without_timestamp(self, tmp_path):
        import datetime
        rec = _faithfulness_record(0.1, False, ["Undated claim"], query="Undated?")
        del rec["timestamp"]
        log = _write_faithfulness_log(tmp_path, [rec])
        cutoff = datetime.datetime(2026, 6, 15)
        assert analysis_agent.build_hallucination_section(log_path=log, since=cutoff) is None
