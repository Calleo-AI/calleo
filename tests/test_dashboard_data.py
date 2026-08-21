"""
Unit tests for the dashboard's token aggregation.

Token counts were added to the conversation-log metadata after the collection
had already been in use, so the aggregation must tolerate rows that predate it:
an old row carries no token keys at all and must be excluded from the average
rather than counted as a zero, which would silently deflate the number.

A tiny stand-in collection is used instead of real ChromaDB, following the
FakeCollection pattern in test_create_db.py.
"""
import os
import sys
from datetime import datetime
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_chatbot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

with patch("chatbot.get_chroma_db"):
    import dashboard_data


class FakeConversations:
    """Minimal ChromaDB collection stand-in exposing only .get()."""

    def __init__(self, rows):
        self._rows = rows

    def get(self):
        return {
            "documents": [f"User: {q}\nAI: {a}" for q, a, _ in self._rows],
            "metadatas": [meta for _, _, meta in self._rows],
        }


def _meta(prompt_tokens=None, completion_tokens=None):
    meta = {"role": "interaction", "timestamp": datetime.now().isoformat()}
    if prompt_tokens is not None:
        meta["prompt_tokens"] = prompt_tokens
        meta["completion_tokens"] = completion_tokens
    return meta


def _kpis(rows):
    return dashboard_data.compute_dashboard(FakeConversations(rows), "all")["kpis"]


class TestTokenAggregation:
    def test_sums_and_averages_billed_turns(self):
        kpis = _kpis([
            ("q1", "a1", _meta(1000, 100)),
            ("q2", "a2", _meta(500, 50)),
        ])
        assert kpis["total_prompt_tokens"] == 1500
        assert kpis["total_completion_tokens"] == 150
        assert kpis["avg_tokens_per_chat"] == 825

    def test_rows_predating_token_accounting_are_excluded(self):
        # The old row must not count as a billed chat: including it would halve
        # the average and misreport what a turn actually costs.
        kpis = _kpis([
            ("old", "a", _meta()),
            ("new", "a", _meta(1000, 100)),
        ])
        assert kpis["total_prompt_tokens"] == 1000
        assert kpis["avg_tokens_per_chat"] == 1100

    def test_unbilled_turns_are_excluded_from_the_average(self):
        # Greetings log real zeros rather than omitting the keys; they are not
        # billed turns either, so they must not drag the average down.
        kpis = _kpis([
            ("hello", "hi there", _meta(0, 0)),
            ("q", "a", _meta(1000, 100)),
        ])
        assert kpis["avg_tokens_per_chat"] == 1100

    def test_no_billed_rows_reports_zero_not_a_crash(self):
        kpis = _kpis([("old", "a", _meta())])
        assert kpis["total_prompt_tokens"] == 0
        assert kpis["avg_tokens_per_chat"] == 0

    def test_empty_collection_reports_zero(self):
        kpis = _kpis([])
        assert kpis["total_prompt_tokens"] == 0
        assert kpis["avg_tokens_per_chat"] == 0
