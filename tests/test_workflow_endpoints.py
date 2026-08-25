"""Endpoint tests for the guided-workflow API in server.py.

Follows the house pattern from test_server.py: env vars set before any import
that transitively loads chatbot.py, and chatbot.get_chroma_db patched so the
module-level DB calls in server.py return a mock.

Several tests here assert on things the workflow routes deliberately do NOT do
— touch the spam tracker, log to the conversations collection, run the
faithfulness judge. Those are the reasons the feature has its own routes rather
than a mode flag on /chat, so they are worth pinning down.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent_chatbot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import llm_client
import site_config
import workflow_engine
import workflow_specs

_mock_collection = MagicMock()
_mock_collection.count.return_value = 0

with patch("chatbot.get_chroma_db", return_value=_mock_collection):
    import server

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "workflows")

SUFFICIENT = json.dumps({
    "sufficient": True, "value": "captured", "confidence": "measured",
    "follow_up": "", "intent": "answer",
})
INSUFFICIENT = json.dumps({
    "sufficient": False, "value": "", "confidence": "unknown",
    "follow_up": "Could you be more specific?", "intent": "answer",
})


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    # Flask-Limiter resolves RATELIMIT_ENABLED once during init_app, so setting
    # the config key after the fact has no effect — the attribute is the lever.
    # Without this, a test class making more than 10 /start calls a minute gets
    # 429s instead of the behaviour it is asserting on.
    server.limiter.enabled = False
    server.limiter.reset()
    server.full_database = _mock_collection
    server.full_database_conversations = _mock_collection
    with server.app.test_client() as c:
        yield c
    server.limiter.enabled = True


@pytest.fixture(autouse=True)
def fixture_registry():
    """Point the registry at the test fixtures, not the shipped specs.

    workflows/*.json is meant to be edited by deployers, so binding endpoint
    assertions to it would break the suite on a legitimate content change.
    """
    workflow_specs.load_all(FIXTURE_DIR)
    server._workflow_registry_loaded = True
    yield
    workflow_specs.load_all()
    server._workflow_registry_loaded = True


@pytest.fixture(autouse=True)
def clear_spam_tracker():
    server.spam_tracker.clear()
    yield
    server.spam_tracker.clear()


def start_run(client, workflow_id="fixture_full"):
    response = client.post("/api/workflow/start", json={"workflow_id": workflow_id})
    assert response.status_code == 200
    return response.get_json()


def answer(client, state, text, reply=SUFFICIENT, workflow_id="fixture_full"):
    with patch.object(llm_client, "chat", return_value=reply):
        return client.post("/api/workflow/turn", json={
            "workflow_id": workflow_id, "state": state, "message": text,
        })


# ---------------------------------------------------------------------------
# GET /api/workflows
# ---------------------------------------------------------------------------

class TestWorkflowsList:
    def test_returns_the_registered_workflows(self, client):
        data = client.get("/api/workflows").get_json()
        assert [w["id"] for w in data["workflows"]] == ["fixture_full"]

    def test_summary_carries_what_the_launcher_needs(self, client):
        entry = client.get("/api/workflows").get_json()["workflows"][0]
        for key in ("id", "title", "description", "estimated_minutes",
                    "sections", "questions", "spec_hash"):
            assert key in entry

    def test_never_exposes_the_output_template(self, client):
        entry = client.get("/api/workflows").get_json()["workflows"][0]
        assert "output_template" not in entry

    def test_empty_list_when_disabled(self, client, monkeypatch):
        monkeypatch.setattr(server.site_config_module, "WORKFLOWS_ENABLED", False)
        assert client.get("/api/workflows").get_json() == {"workflows": []}

    def test_makes_no_llm_call(self, client):
        with patch.object(llm_client, "chat", side_effect=AssertionError("no LLM here")):
            assert client.get("/api/workflows").status_code == 200


# ---------------------------------------------------------------------------
# POST /api/workflow/start
# ---------------------------------------------------------------------------

class TestStart:
    def test_returns_intro_and_first_question(self, client):
        data = start_run(client)
        assert len(data["messages"]) == 2
        assert "What is your name?" in data["messages"][-1]["content"]

    def test_makes_no_llm_call(self, client):
        with patch.object(llm_client, "chat", side_effect=AssertionError("no LLM on start")):
            assert start_run(client)["status"] == "in_progress"

    def test_unknown_workflow_is_404(self, client):
        response = client.post("/api/workflow/start", json={"workflow_id": "nope"})
        assert response.status_code == 404
        assert response.get_json()["error"] == "unknown_workflow"

    def test_missing_workflow_id_is_404(self, client):
        assert client.post("/api/workflow/start", json={}).status_code == 404

    def test_503_when_workflows_are_disabled(self, client, monkeypatch):
        monkeypatch.setattr(server.site_config_module, "WORKFLOWS_ENABLED", False)
        response = client.post("/api/workflow/start", json={"workflow_id": "fixture_full"})
        assert response.status_code == 503
        assert response.get_json()["error"] == "workflows_disabled"

    def test_state_is_json_round_trippable(self, client):
        state = start_run(client)["state"]
        assert json.loads(json.dumps(state))["workflow_id"] == "fixture_full"


# ---------------------------------------------------------------------------
# POST /api/workflow/turn
# ---------------------------------------------------------------------------

class TestTurn:
    def test_a_sufficient_answer_advances(self, client):
        state = start_run(client)["state"]
        data = answer(client, state, "Jo Blake").get_json()
        assert data["progress"]["answered"] == 1
        assert "How many per quarter?" in data["messages"][-1]["content"]

    def test_an_insufficient_answer_re_asks_without_advancing(self, client):
        state = start_run(client)["state"]
        data = answer(client, state, "hmm", reply=INSUFFICIENT).get_json()
        assert data["progress"]["answered"] == 0
        assert data["messages"][-1]["content"] == "Could you be more specific?"

    def test_the_follow_up_budget_forces_an_advance(self, client):
        state = start_run(client)["state"]
        for _ in range(3):
            state = answer(client, state, "hmm", reply=INSUFFICIENT).get_json()["state"]
        assert workflow_engine.current_question(
            workflow_specs.get("fixture_full")["spec"], state)["question"]["id"] == "count"

    def test_a_missing_message_is_400(self, client):
        state = start_run(client)["state"]
        response = client.post("/api/workflow/turn",
                               json={"workflow_id": "fixture_full", "state": state})
        assert response.status_code == 400

    def test_a_blank_message_is_400(self, client):
        state = start_run(client)["state"]
        response = client.post("/api/workflow/turn", json={
            "workflow_id": "fixture_full", "state": state, "message": "   "})
        assert response.status_code == 400

    def test_a_tampered_state_version_is_400(self, client):
        state = start_run(client)["state"]
        state["v"] = 99
        assert answer(client, state, "hi").status_code == 400

    def test_a_mismatched_workflow_id_in_state_is_400(self, client):
        state = start_run(client)["state"]
        state["workflow_id"] = "something_else"
        assert answer(client, state, "hi").status_code == 400

    def test_an_oversized_state_is_413(self, client):
        state = start_run(client)["state"]
        state["answers"] = {"basics": {"name": {"raw": "x" * 200000, "status": "answered"}}}
        assert answer(client, state, "hi").status_code == 413

    def test_a_stale_spec_hash_is_409_with_migrated_answers(self, client):
        state = start_run(client)["state"]
        state = answer(client, state, "Jo Blake").get_json()["state"]
        state["spec_hash"] = "0" * 12
        response = answer(client, state, "next")
        assert response.status_code == 409
        data = response.get_json()
        assert site_config.WORKFLOW_SPEC_CHANGED_MESSAGE in data["messages"][0]["content"]
        # The answer given before the edit survives the migration.
        assert data["state"]["answers"]["basics"]["name"]["value"] == "captured"

    def test_a_provider_failure_still_advances(self, client):
        """A model outage must never strand someone mid-interview."""
        state = start_run(client)["state"]
        with patch.object(llm_client, "chat", side_effect=RuntimeError("provider down")):
            response = client.post("/api/workflow/turn", json={
                "workflow_id": "fixture_full", "state": state, "message": "Jo"})
        assert response.status_code == 200
        assert response.get_json()["progress"]["answered"] == 1

    def test_malformed_judge_json_fails_open(self, client):
        state = start_run(client)["state"]
        data = answer(client, state, "Jo", reply="I'm not going to answer that").get_json()
        assert data["progress"]["answered"] == 1

    def test_completion_returns_the_document(self, client):
        state = start_run(client)["state"]
        for text in ["Jo", "40", "New", "Draft", "2h", "no", "none"]:
            state = answer(client, state, text).get_json()["state"]
        response = client.post("/api/workflow/document",
                               json={"workflow_id": "fixture_full", "state": state})
        document = response.get_json()["document"]
        assert document["filename"].endswith(".md")
        assert "# Fixture" in document["body"]

    def test_skip_advances_without_an_llm_call(self, client):
        state = start_run(client)["state"]
        with patch.object(llm_client, "chat", side_effect=AssertionError("no LLM for a skip")):
            response = client.post("/api/workflow/turn", json={
                "workflow_id": "fixture_full", "state": state, "message": "skip"})
        assert response.status_code == 200
        assert response.get_json()["state"]["answers"]["basics"]["name"]["status"] == "skipped"


# ---------------------------------------------------------------------------
# What the workflow routes must NOT touch
# ---------------------------------------------------------------------------

class TestIsolationFromChatPipeline:
    def test_repeated_identical_answers_never_trip_spam_detection(self, client):
        """The duplicate rule blocks a third identical /chat message in 5 minutes.

        "I don't know" three times is entirely normal in an interview, so the
        workflow routes must not go anywhere near check_spam.
        """
        state = start_run(client)["state"]
        for _ in range(5):
            response = client.post("/api/workflow/turn", json={
                "workflow_id": "fixture_full", "state": state, "message": "I don't know"})
            assert response.status_code == 200
        assert len(server.spam_tracker) == 0

    def test_a_symbol_heavy_answer_is_not_rejected_as_gibberish(self, client):
        """is_gibberish("~40% / 60% (+-5)") is under 50% alphanumeric."""
        noisy = "~40% / 60%  (+-5)"
        assert server.is_gibberish(noisy) is True   # it would be rejected by /chat
        state = start_run(client)["state"]
        data = answer(client, state, noisy).get_json()
        assert data["progress"]["answered"] == 1    # but the workflow accepts it

    def test_turns_are_never_logged_to_the_conversations_collection(self, client):
        _mock_collection.add.reset_mock()
        state = start_run(client)["state"]
        answer(client, state, "Jo Blake")
        assert _mock_collection.add.call_count == 0

    def test_the_faithfulness_judge_never_runs(self, client):
        state = start_run(client)["state"]
        with patch.object(server, "score_faithfulness_async") as scorer:
            answer(client, state, "Jo Blake")
        assert scorer.call_count == 0


# ---------------------------------------------------------------------------
# POST /api/workflow/submit
# ---------------------------------------------------------------------------

class TestSubmit:
    def test_writes_the_response_to_disk(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKFLOW_RESPONSES_DIR", str(tmp_path))
        state = start_run(client)["state"]
        state = answer(client, state, "Jo Blake").get_json()["state"]

        response = client.post("/api/workflow/submit",
                               json={"workflow_id": "fixture_full", "state": state})
        data = response.get_json()
        assert data["saved"] is True
        written = list(tmp_path.iterdir())
        assert len(written) == 1
        assert "# Fixture" in written[0].read_text(encoding="utf-8")

    def test_does_not_email_unless_asked(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKFLOW_RESPONSES_DIR", str(tmp_path))
        state = start_run(client)["state"]
        with patch.object(server.email_report, "send_markdown_email") as sender:
            data = client.post("/api/workflow/submit", json={
                "workflow_id": "fixture_full", "state": state}).get_json()
        assert data["emailed"] is False
        assert sender.call_count == 0

    def test_emails_when_asked_and_configured(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKFLOW_RESPONSES_DIR", str(tmp_path))
        state = start_run(client)["state"]
        with patch.object(server.email_report, "send_markdown_email",
                          return_value=True) as sender:
            data = client.post("/api/workflow/submit", json={
                "workflow_id": "fixture_full", "state": state, "email": True}).get_json()
        assert data["emailed"] is True
        subject = sender.call_args[0][0]
        assert "Fixture workflow" in subject

    def test_reports_emailed_false_when_smtp_is_unconfigured(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKFLOW_RESPONSES_DIR", str(tmp_path))
        monkeypatch.delenv("SENDER_EMAIL", raising=False)
        monkeypatch.delenv("SENDER_PASSWORD", raising=False)
        monkeypatch.delenv("RECIPIENT_EMAIL", raising=False)
        state = start_run(client)["state"]
        data = client.post("/api/workflow/submit", json={
            "workflow_id": "fixture_full", "state": state, "email": True}).get_json()
        assert data["emailed"] is False

    def test_a_hostile_filename_answer_cannot_escape_the_directory(
            self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKFLOW_RESPONSES_DIR", str(tmp_path))
        state = start_run(client)["state"]
        malicious = json.dumps({
            "sufficient": True, "value": "../../../../tmp/pwned",
            "confidence": "unknown", "follow_up": "", "intent": "answer",
        })
        state = answer(client, state, "whatever", reply=malicious).get_json()["state"]
        client.post("/api/workflow/submit",
                    json={"workflow_id": "fixture_full", "state": state})
        written = [p.name for p in tmp_path.iterdir()]
        assert len(written) == 1
        assert "/" not in written[0] and ".." not in written[0]

    def test_an_unwritable_directory_reports_saved_false_rather_than_500(
            self, client, monkeypatch, tmp_path):
        # A file where a directory must go: os.makedirs then fails on every
        # platform. The previous "/proc/nope/cannot-write" is simply creatable
        # on Windows, so the write succeeded and this asserted the opposite of
        # what it was checking.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setenv("WORKFLOW_RESPONSES_DIR", str(blocker / "responses"))
        state = start_run(client)["state"]
        response = client.post("/api/workflow/submit",
                               json={"workflow_id": "fixture_full", "state": state})
        assert response.status_code == 200
        assert response.get_json()["saved"] is False


# ---------------------------------------------------------------------------
# Static assets and the rate-limit envelope
# ---------------------------------------------------------------------------

class TestStaticAssets:
    @pytest.mark.parametrize("path", [
        "/chatbot.css", "/chatbot.js", "/site_config.js", "/chat_history_store.js",
        "/voice_input.js", "/workflow_client.js", "/chatbot_iframe.html",
    ])
    def test_widget_assets_are_served(self, client, path):
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", [
        "/server.py", "/nonexistent.js", "/site_config.py",
    ])
    def test_anything_outside_the_allowlist_is_404(self, client, path):
        """The allowlist is what keeps the catch-all route from serving the repo."""
        assert client.get(path).status_code == 404

    def test_the_existing_routes_still_resolve(self, client):
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


class TestRateLimitEnvelope:
    def test_workflow_routes_get_a_real_429(self, client):
        with server.app.test_request_context("/api/workflow/turn"):
            response, status = server.ratelimit_handler(None)
            assert status == 429
            assert response.get_json()["error"] == "rate_limited"

    def test_chat_keeps_its_200_envelope(self, client):
        """The widget renders whatever /chat returns as a bubble — don't change it."""
        with server.app.test_request_context("/chat"):
            response, status = server.ratelimit_handler(None)
            assert status == 200
            assert "too many messages" in response.get_json()["response"]
