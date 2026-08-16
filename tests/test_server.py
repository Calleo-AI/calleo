"""
Unit tests for server.py

Tests spam detection helpers (is_gibberish, cleanup_old_messages, check_spam),
query contextualization, and Flask endpoint behaviour.

The module-level ChromaDB initialisation in server.py is intercepted by
patching chatbot.get_chroma_db before the module is imported.
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

# Set env vars before any import that transitively loads chatbot.py so that
# the module-level OpenAI client does not raise AuthenticationError.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_chatbot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import llm_client  # the single LLM seam; tests patch llm_client.chat
import school_config  # canned messages: tests assert against the same constants the server uses

# Patch chatbot.get_chroma_db so the four module-level DB calls in server.py
# return a mock collection instead of hitting real ChromaDB/Gemini APIs.
_mock_collection = MagicMock()
_mock_collection.count.return_value = 0

with patch("chatbot.get_chroma_db", return_value=_mock_collection):
    import server

from server import is_gibberish, cleanup_old_messages, check_spam, spam_tracker


# ---------------------------------------------------------------------------
# Fixture: reset global spam state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_spam_tracker():
    spam_tracker.clear()
    yield
    spam_tracker.clear()


# ---------------------------------------------------------------------------
# is_gibberish
# ---------------------------------------------------------------------------

class TestIsGibberish:
    def test_normal_sentence_is_not_gibberish(self):
        assert is_gibberish("What are the school hours?") is False

    def test_message_10_chars_or_fewer_never_gibberish(self):
        assert is_gibberish("!!!!!!!!!!") is False  # exactly 10 symbols
        assert is_gibberish("a!!!!!!!") is False    # 9 chars

    def test_11_char_mostly_symbols_is_gibberish(self):
        # 1 alphanumeric out of 11 = ~9 % < 50 %
        assert is_gibberish("a!!!!!!!!!!") is True

    def test_exactly_50_percent_alphanumeric_not_gibberish(self):
        # 6 alpha + 6 symbols = 50 %, not *less than* 50 %
        assert is_gibberish("abcdef!!!!!!") is False

    def test_below_50_percent_alphanumeric_is_gibberish(self):
        # 5 alpha + 7 symbols = ~41.7 %
        assert is_gibberish("abcde!!!!!!!") is True

    def test_all_alphanumeric_not_gibberish(self):
        assert is_gibberish("HelloWorld12345") is False

    def test_empty_string_not_gibberish(self):
        assert is_gibberish("") is False

    def test_keyboard_mash_is_gibberish(self):
        assert is_gibberish("!@#$%^&*()_+-=[]{}") is True

    def test_sentence_with_spaces_not_gibberish(self):
        # Spaces are non-alphanumeric but "hello world" is still mostly letters
        assert is_gibberish("hello world") is False


# ---------------------------------------------------------------------------
# cleanup_old_messages
# ---------------------------------------------------------------------------

class TestCleanupOldMessages:
    def test_removes_messages_older_than_window(self):
        fp = "fp1"
        spam_tracker[fp].append((time.time() - 400, "old"))
        cleanup_old_messages(fp)
        assert len(spam_tracker[fp]) == 0

    def test_keeps_messages_within_window(self):
        fp = "fp2"
        spam_tracker[fp].append((time.time() - 100, "recent"))
        cleanup_old_messages(fp)
        assert len(spam_tracker[fp]) == 1

    def test_removes_old_keeps_recent_when_mixed(self):
        fp = "fp3"
        spam_tracker[fp].extend([
            (time.time() - 400, "old"),
            (time.time() - 100, "recent"),
        ])
        cleanup_old_messages(fp)
        assert len(spam_tracker[fp]) == 1
        assert spam_tracker[fp][0][1] == "recent"

    def test_custom_max_age_respected(self):
        fp = "fp4"
        spam_tracker[fp].append((time.time() - 90, "msg"))
        cleanup_old_messages(fp, max_age_seconds=60)
        assert len(spam_tracker[fp]) == 0


# ---------------------------------------------------------------------------
# check_spam
# ---------------------------------------------------------------------------

class TestCheckSpam:
    def test_legitimate_message_not_spam(self):
        is_spam, spam_type = check_spam("u1", "What are the school fees?")
        assert is_spam is False
        assert spam_type is None

    def test_gibberish_detected(self):
        is_spam, spam_type = check_spam("u1", "!@#$%^&*()_+-=[]{}!!!")
        assert is_spam is True
        assert spam_type == "gibberish"

    def test_duplicate_detected_on_third_identical_send(self):
        fp = "u_dup"
        msg = "Tell me about tuition"
        check_spam(fp, msg)
        check_spam(fp, msg)
        is_spam, spam_type = check_spam(fp, msg)
        assert is_spam is True
        assert spam_type == "duplicate"

    def test_first_two_identical_messages_not_duplicate(self):
        fp = "u_dup2"
        msg = "Hello school"
        check_spam(fp, msg)
        is_spam, _ = check_spam(fp, msg)
        assert is_spam is False

    def test_duplicate_check_is_case_insensitive(self):
        fp = "u_case"
        check_spam(fp, "Hello school")
        check_spam(fp, "hello school")
        is_spam, spam_type = check_spam(fp, "HELLO SCHOOL")
        assert is_spam is True
        assert spam_type == "duplicate"

    def test_rapid_fire_detected_after_ten_messages(self):
        fp = "u_rapid"
        for i in range(10):
            check_spam(fp, f"unique message {i}")
        is_spam, spam_type = check_spam(fp, "one more")
        assert is_spam is True
        assert spam_type == "too_fast"

    def test_nine_messages_not_rapid_fire(self):
        fp = "u_ok"
        for i in range(9):
            check_spam(fp, f"unique message {i}")
        is_spam, _ = check_spam(fp, "tenth unique message")
        assert is_spam is False

    def test_gibberish_takes_priority_over_duplicate(self):
        fp = "u_gib"
        gibberish = "!@#$%^&*()_+-=[]{}!!!"
        check_spam(fp, gibberish)
        is_spam, spam_type = check_spam(fp, gibberish)
        assert spam_type == "gibberish"

    def test_legitimate_message_is_recorded(self):
        fp = "u_rec"
        check_spam(fp, "Valid question about the school")
        assert len(spam_tracker[fp]) == 1

    def test_gibberish_message_is_not_recorded(self):
        fp = "u_norec"
        check_spam(fp, "!@#$%^&*()_+-=[]{}!!!")
        assert len(spam_tracker[fp]) == 0


# ---------------------------------------------------------------------------
# contextualize_query
# ---------------------------------------------------------------------------

class TestContextualizeQuery:
    def test_returns_original_query_when_no_history(self):
        result = server.contextualize_query([], "What are school hours?")
        assert result == "What are school hours?"

    def test_calls_llm_and_returns_rephrased_query(self, monkeypatch):
        monkeypatch.setattr(
            llm_client, "chat", lambda *a, **k: "  Rephrased standalone question  ")
        history = [{"role": "user", "content": "Tell me about fees"}]
        result = server.contextualize_query(history, "What about deadlines?")
        assert result == "Rephrased standalone question"

    def test_returns_original_query_on_llm_exception(self, monkeypatch):
        def raise_error(*a, **k):
            raise Exception("API error")
        monkeypatch.setattr(llm_client, "chat", raise_error)
        history = [{"role": "user", "content": "Some context"}]
        result = server.contextualize_query(history, "Follow-up question?")
        assert result == "Follow-up question?"

    def test_only_last_six_history_messages_used(self, monkeypatch):
        captured = {}

        def fake_chat(messages, *a, **k):
            captured["prompt"] = messages[1]["content"]
            return "standalone"

        monkeypatch.setattr(llm_client, "chat", fake_chat)
        history = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
        server.contextualize_query(history, "latest question")
        assert "msg 0" not in captured["prompt"]
        assert "msg 9" in captured["prompt"]


# ---------------------------------------------------------------------------
# Flask endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    # Disable Flask-Limiter so rate-limit responses don't mask spam/logic tests.
    server.app.config["RATELIMIT_ENABLED"] = False
    server.full_database = _mock_collection
    server.conversations_db = _mock_collection
    server.full_database_conversations = _mock_collection
    with server.app.test_client() as c:
        yield c
    server.app.config["RATELIMIT_ENABLED"] = True


class TestChatEndpoint:
    def test_missing_message_returns_400(self, client):
        resp = client.post("/chat", json={})
        assert resp.status_code == 400

    def test_greeting_hello_returns_200_with_school_mention(self, client):
        resp = client.post("/chat", json={"message": "hello"})
        assert resp.status_code == 200
        assert resp.get_json()["response"] == school_config.GREETING_MESSAGE
        assert school_config.SCHOOL_NAME in resp.get_json()["response"]

    def test_greeting_hi_returns_200(self, client):
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.status_code == 200

    def test_greeting_hey_returns_200(self, client):
        resp = client.post("/chat", json={"message": "hey"})
        assert resp.status_code == 200

    def test_no_db_returns_503(self, client):
        original = server.full_database
        server.full_database = None
        resp = client.post("/chat", json={"message": "hello"})
        server.full_database = original
        assert resp.status_code == 503

    def test_retrieval_error_returns_503_without_llm_call(self, client, monkeypatch):
        monkeypatch.setattr(
            server, "get_relevant_documents",
            lambda q, db: ("Error retrieving documents.", []),
        )
        llm = MagicMock(side_effect=AssertionError("LLM must not be called on retrieval error"))
        monkeypatch.setattr(llm_client, "chat", llm)
        resp = client.post(
            "/chat",
            json={"message": "tell me about the athletics program"},
            headers={"User-Agent": "RetrievalErrorTest/1.0"},
        )
        assert resp.status_code == 503
        assert "try again" in resp.get_json()["response"].lower()
        llm.assert_not_called()

    def test_gibberish_message_returns_spam_response(self, client):
        resp = client.post(
            "/chat",
            json={"message": "!@#$%^&*()_+-=[]{}!!!"},
            headers={"User-Agent": "GibberishDetectionTest/1.0"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "valid question" in data["response"].lower()


# ---------------------------------------------------------------------------
# Language parameter forwarding
# ---------------------------------------------------------------------------

def _setup_non_greeting(monkeypatch):
    """Patch get_relevant_documents and the LLM call for non-greeting flow."""
    monkeypatch.setattr(server, "get_relevant_documents", lambda q, db: ("School passage.", []))
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k: "Test answer.")


class TestChatEndpointLanguage:
    def test_language_defaults_to_english_when_omitted(self, client, monkeypatch):
        _setup_non_greeting(monkeypatch)
        captured = {}

        def fake_make_prompt(query, passage, history=[], language="English"):
            captured["language"] = language
            return "ANSWER: "

        monkeypatch.setattr(server, "make_prompt", fake_make_prompt)
        client.post("/chat", json={"message": "What are the school hours?"})
        assert captured.get("language") == "English"

    def test_language_forwarded_to_make_prompt(self, client, monkeypatch):
        _setup_non_greeting(monkeypatch)
        captured = {}

        def fake_make_prompt(query, passage, history=[], language="English"):
            captured["language"] = language
            return "ANSWER: "

        monkeypatch.setattr(server, "make_prompt", fake_make_prompt)
        client.post("/chat", json={"message": "What are the school hours?", "language": "French"})
        assert captured.get("language") == "French"


# ---------------------------------------------------------------------------
# /generate-title endpoint
# ---------------------------------------------------------------------------

class TestGenerateTitleEndpoint:
    def setup_method(self):
        server.app.testing = True
        self.client = server.app.test_client()

    def test_missing_message_returns_400(self):
        resp = self.client.post("/generate-title", json={"language": "English"})
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"].lower()

    def test_empty_message_returns_400(self):
        resp = self.client.post("/generate-title",
                                 json={"message": "   ", "language": "English"})
        assert resp.status_code == 400

    def test_message_over_2000_chars_returns_400(self):
        resp = self.client.post("/generate-title",
                                 json={"message": "a" * 2001, "language": "English"})
        assert resp.status_code == 400

    def test_successful_title_generation(self):
        with patch("server.generate_title", return_value="Tuition fees"):
            resp = self.client.post("/generate-title",
                                     json={"message": "How much is tuition?",
                                           "language": "English"})
            assert resp.status_code == 200
            assert resp.get_json()["title"] == "Tuition fees"

    def test_llm_failure_returns_fallback_200(self):
        # Upstream throws — endpoint should degrade gracefully with 200 + fallback.
        with patch("server.generate_title", side_effect=RuntimeError("upstream")):
            resp = self.client.post("/generate-title",
                                     json={"message": "How much is tuition?",
                                           "language": "English"})
            assert resp.status_code == 200
            assert "title" in resp.get_json()
            assert resp.get_json()["title"]  # non-empty



# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------

import base64
from datetime import datetime, timedelta

# Dashboard data is now derived from the ChromaDB ``full_database_conversations``
# collection (the same source the daily analysis report uses), so these tests
# feed a fake collection rather than mocking Postgres.

_AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}


class _FakeCollection:
    """Minimal stand-in for a ChromaDB collection's ``.get()``."""

    def __init__(self, documents, metadatas):
        self._documents = documents
        self._metadatas = metadatas

    def get(self, *args, **kwargs):
        return {
            "documents": list(self._documents),
            "metadatas": list(self._metadatas),
            "ids": [str(i) for i in range(len(self._documents))],
        }


def _iso(minutes_ago):
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()


def _sample_collection():
    """Four interactions, each in its own session (timestamps >30m apart)."""
    docs = [
        "User: How do I apply to Example School?\nAI: You can apply online.",
        "User: What is the tuition cost?\nAI: Tuition is $30,000.",
        "User: Do you have a basketball team?\nAI: We field many competitive teams.",
        f"User: How do I apply?\nAI: {school_config.DEFERRAL_MESSAGE}",
    ]
    metas = [
        {"timestamp": _iso(200)},
        {"timestamp": _iso(160)},
        {"timestamp": _iso(120)},
        {"timestamp": _iso(80)},
    ]
    return _FakeCollection(docs, metas)


class TestDashboardEndpoints:
    def test_dashboard_returns_kpis_and_events(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())

        resp = client.get("/api/dashboard", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["kpis"]["total_chats"] == 4
        assert data["kpis"]["error_count"] == 1          # the fallback response
        assert data["kpis"]["unique_sessions"] == 4
        assert len(data["recent_events"]) == 4
        assert "hourly_traffic" in data
        assert len(data["hourly_traffic"]) == 24

    def test_dashboard_7d_returns_daily_traffic(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/dashboard?range=7d", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "daily_traffic" in data
        assert len(data["daily_traffic"]) == 7
        assert {"day", "total"} <= set(data["daily_traffic"][0].keys())

    def test_conversations_returns_session_list(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/conversations", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 4
        statuses = {c["status"] for c in data}
        assert "error" in statuses and "success" in statuses

    def test_event_detail_returns_event(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/events/0", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == 0
        assert data["user_message_preview"] == "How do I apply to Example School?"
        assert data["model"] == "qwen/qwen3.5-397b-a17b"

    def test_event_detail_returns_404_for_missing_event(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/events/999", headers=_AUTH)
        assert resp.status_code == 404

    def test_session_detail_returns_messages(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/dashboard/sessions/session-0000", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["session_id"] == "session-0000"
        assert data["message_count"] == 2  # 1 user + 1 assistant
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"

    def test_session_detail_returns_empty_for_no_session(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/dashboard/sessions/unknown", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["message_count"] == 0
        assert data["messages"] == []

    def test_dashboard_topics_returns_topic_list(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/dashboard/topics", headers=_AUTH)
        assert resp.status_code == 200
        topics = {t["topic"]: t["count"] for t in resp.get_json()}
        assert topics.get("Admissions") == 2  # two sessions open with an apply question
        assert "Tuition & Fees" in topics
        assert "Athletics" in topics

    def test_dashboard_topic_threads_returns_matching_sessions(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/dashboard/topics/Admissions/threads", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        assert {"session-0000", "session-0003"} == {t["session_id"] for t in data}

    def test_dashboard_topic_threads_returns_empty_for_unknown_topic(self, client, monkeypatch):
        monkeypatch.setattr(server, "full_database_conversations", _sample_collection())
        resp = client.get("/api/dashboard/topics/Astronomy/threads", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_analysis_flags_frustrated_and_reports_model(self, client, monkeypatch):
        # One session with a repeated question + two fallbacks -> frustrated.
        docs = [
            f"User: where is the school\nAI: {school_config.DEFERRAL_MESSAGE}",
            f"User: where is the school\nAI: {school_config.DEFERRAL_MESSAGE}",
            "User: what programs do you offer\nAI: We offer a broad academic program.",
        ]
        metas = [{"timestamp": _iso(10)}, {"timestamp": _iso(9)}, {"timestamp": _iso(8)}]
        monkeypatch.setattr(server, "full_database_conversations", _FakeCollection(docs, metas))

        resp = client.get("/api/dashboard/analysis", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["frustrated"]["count"] == 1
        assert data["frustrated"]["session_ids"] == ["session-0000"]
        model = data["model_comparison"][0]
        assert model["requests"] == 3
        assert model["avg_response_words"] > 0


# ---------------------------------------------------------------------------
# Config invariants
# ---------------------------------------------------------------------------

def test_deferral_message_is_a_dashboard_fallback_marker():
    """server.py's canned deferral and dashboard_data's fallback classifier must
    stay the same constant — otherwise deferred answers stop being counted as
    unanswered questions on the dashboard."""
    import dashboard_data
    assert school_config.DEFERRAL_MESSAGE.lower() in dashboard_data.FALLBACK_MARKERS
