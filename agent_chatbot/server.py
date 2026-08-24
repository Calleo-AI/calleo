import sys
import os
import json
import chromadb
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import uuid
from datetime import datetime
import hashlib
import time
import threading
from collections import defaultdict

# Add Database folder to path BEFORE importing modules from it
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Database"))
# Add agent_analysis folder to path so we can import the faithfulness scorer
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent_analysis"))
# Add repo root so we can import the centralized llm_client
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import llm_client
from snapshot_db import list_snapshots
from chatbot import (
    get_chroma_db, get_relevant_documents, make_prompt, _chroma_client,
    generate_title, _fallback_title,
)
from site_config import (
    SITE_FACTS,
    SITE_SHORT_NAME,
    GREETING_MESSAGE,
    DEFERRAL_MESSAGE,
    SPAM_GIBBERISH_MESSAGE,
)
from faithfulness_scorer import score_faithfulness_async
import dashboard_data
import workflow_specs
import workflow_engine
# The whole module, not just names: the workflow routes read config values that
# a test may monkeypatch, and `from x import y` would freeze them at import.
import site_config as site_config_module
# Reused by /api/workflow/submit to email a completed response.
import email_report



# --- Client Fingerprinting ---
def get_client_fingerprint():
    """Generate a unique fingerprint for a client based on request headers."""
    ip = request.remote_addr or ''
    user_agent = request.headers.get('User-Agent', '')
    accept_language = request.headers.get('Accept-Language', '')
    accept_encoding = request.headers.get('Accept-Encoding', '')

    fingerprint_data = f"{ip}|{user_agent}|{accept_language}|{accept_encoding}"
    fingerprint_hash = hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16]
    return fingerprint_hash

# --- Spam Detection ---
# Store message history per fingerprint: {fingerprint: [(timestamp, message), ...]}
spam_tracker = defaultdict(list)

def cleanup_old_messages(fingerprint, max_age_seconds=300):
    """Remove messages older than max_age_seconds (default 5 minutes)."""
    current_time = time.time()
    spam_tracker[fingerprint] = [
        (ts, msg) for ts, msg in spam_tracker[fingerprint]
        if current_time - ts < max_age_seconds
    ]

def is_gibberish(message):
    """Check if message is gibberish (less than 50% alphanumeric, only for messages > 10 chars)."""
    if len(message) <= 10:
        return False
    alphanumeric_count = sum(1 for c in message if c.isalnum())
    return alphanumeric_count / len(message) < 0.5

def check_spam(fingerprint, message):
    """
    Check if message is spam. Returns (is_spam, spam_type) tuple.
    spam_type: 'duplicate', 'too_fast', 'gibberish', or None
    """
    current_time = time.time()

    # Clean up old entries first
    cleanup_old_messages(fingerprint)

    # Check for gibberish
    if is_gibberish(message):
        return True, 'gibberish'

    # Check for duplicate messages (same message 2+ times within 5 minutes)
    message_lower = message.lower().strip()
    duplicate_count = sum(1 for ts, msg in spam_tracker[fingerprint] if msg.lower().strip() == message_lower)
    if duplicate_count >= 2:
        return True, 'duplicate'

    # Check for rapid-fire messaging (10+ messages within 60 seconds)
    recent_messages = [ts for ts, msg in spam_tracker[fingerprint] if current_time - ts < 60]
    if len(recent_messages) >= 10:
        return True, 'too_fast'

    # Not spam - record this message
    spam_tracker[fingerprint].append((current_time, message))
    return False, None

SPAM_RESPONSES = {
    'duplicate': "Please don't send the same message repeatedly.",
    'too_fast': "You're sending messages too quickly. Please slow down.",
    'gibberish': SPAM_GIBBERISH_MESSAGE
}



app = Flask(__name__)
# CORS: allow all origins with credentials support for cross-origin dashboard hosting
_cors_origins = os.environ.get("CORS_ORIGINS", "*")
if _cors_origins == "*":
    CORS(app, supports_credentials=True)
else:
    CORS(app, supports_credentials=True, origins=[o.strip() for o in _cors_origins.split(",")])

# Initialize Rate Limiter (uses client fingerprint instead of just IP)
limiter = Limiter(
    get_client_fingerprint,
    app=app,
    default_limits=["200 per day", "100 per hour"],
    storage_uri="memory://",
)



@app.errorhandler(429)
def ratelimit_handler(e):
    # /chat's widget renders whatever comes back as a chat bubble, so a rate
    # limit there is deliberately a 200 carrying a human-readable message.
    # The workflow client parses a typed envelope instead and needs the real
    # status code to distinguish throttling from a bad request.
    if request.path.startswith("/api/workflow"):
        return jsonify({"error": "rate_limited", "retry_after": 60}), 429
    return jsonify({"response": "You have sent too many messages recently. Please wait a minute before trying again."}), 200

# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    db_status = "loaded" if full_database else "not loaded"
    db_count = 0
    if full_database:
        try:
            db_count = full_database.count()
        except:
            db_count = "error"
    return jsonify({
        "status": "ok",
        "database": db_status,
        "document_count": db_count
    }), 200

# --- END Health Check ---

# --- Serve chatbot widget files ---
# One handler over an explicit allowlist rather than a route per file: the
# widget gained two more assets (voice input, workflow client) and copy-pasting
# a five-line send_file block each time is how a directory-traversal hole gets
# introduced. The allowlist means an arbitrary path can never be served, and the
# URLs are unchanged so anything already embedded in the wild keeps working.
_FRONTEND_FILES = {
    "chatbot.css",
    "chatbot.js",
    "site_config.js",
    "chat_history_store.js",
    "voice_input.js",
    "workflow_client.js",
    "chatbot_iframe.html",
}


def _serve_frontend(filename):
    if filename not in _FRONTEND_FILES:
        return "File not found", 404
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, "frontend", filename)
    if os.path.exists(path):
        return send_file(path)
    return "File not found", 404


@app.route("/<path:filename>", methods=["GET"])
def serve_frontend_file(filename):
    return _serve_frontend(filename)


# --- Homepage Route ---
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "message": f"{SITE_SHORT_NAME} AI Server is running",
        "endpoints": {
            "health": "/health (GET)",
            "chat": "/chat (POST) - requires JSON body"
        }
    }), 200

print(f"--- {SITE_SHORT_NAME} AI Server Starting ---")

# 3. Initialize the Database
try:
    full_database = get_chroma_db("full_database")
    count = full_database.count()
    print(f"Full database loaded successfully. Documents indexed: {count}")
    
    # Auto-rollback if database is empty — inline so we reuse the shared _chroma_client
    # instead of letting snapshot_db.rollback() spin up another PersistentClient.
    if count == 0:
        print("WARNING: Database is empty! Attempting to rollback from snapshot...")
        try:
            snapshots = list_snapshots("full_database")
            if not snapshots:
                raise RuntimeError("No snapshots available for full_database")

            latest = snapshots[0]
            with open(latest["path"]) as f:
                snapshot_data = json.load(f)

            try:
                _chroma_client.delete_collection("full_database")
                print("[ChromaDB] Deleted existing empty collection: full_database")
            except Exception as delete_error:
                print(f"[ChromaDB] Could not delete collection (may not exist): {delete_error}")

            full_database = get_chroma_db("full_database")

            ids = snapshot_data["ids"]
            documents = snapshot_data["documents"]
            metadatas = snapshot_data["metadatas"]
            embeddings = snapshot_data["embeddings"]

            batch_size = 100
            for i in range(0, len(ids), batch_size):
                full_database.add(
                    ids=ids[i:i + batch_size],
                    documents=documents[i:i + batch_size],
                    metadatas=metadatas[i:i + batch_size],
                    embeddings=embeddings[i:i + batch_size],
                )

            count = full_database.count()
            print(f"Rollback complete. Documents restored: {count}")
        except Exception as rollback_error:
            print(f"Rollback failed: {rollback_error}")
            print("Please run 'python Database/snapshot_db.py --rollback --collection full_database' manually")

except Exception as e:
    print(f"CRITICAL ERROR loading full database: {e}")
    full_database = None

DEFAULT_MODEL = llm_client.model_for("chat")

# Initialize Full Database Conversations Database
try:
    full_database_conversations = get_chroma_db("full_database_conversations")
    print("Full database conversations loaded successfully.")
except Exception as e:
    print(f"Error loading full database conversations: {e}")
    full_database_conversations = None

def contextualize_query(history, latest_query):
    if not history:
        return latest_query
    
    history_text = ""
    # Use only the last 3 turns to keep context focused and reduce token usage
    for msg in history[-6:]: 
        role = "User" if msg['role'] == "user" else "AI"
        history_text += f"{role}: {msg['content']}\n"
    
    prompt = f"""
    Given the following conversation history and a follow-up question, rephrase the follow-up question to be a standalone question that includes all necessary context.
    If the follow-up question is already self-contained, return it unchanged.
    
    Chat History:
    {history_text}
    
    Follow-up Question: {latest_query}
    
    Standalone Question:
    """
    
    try:
        return llm_client.chat(
            [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            role="chat",
            temperature=0,
            reasoning="off",
        ).strip()
    except Exception as e:
        print(f"Error contextualizing query: {e}")
        return latest_query

def get_best_link(query, response_text, sources):
    if not sources:
        return None
    if len(sources) == 1:
        return sources[0]
        
    prompt = f"""Given the user query: "{query}"
And the following response generated:
"{response_text}"

Which of these source links is the MOST relevant to the response?
Links:
{chr(10).join([f"- {s}" for s in sources])}

Please output ONLY the single best URL from the list above, nothing else."""
    
    try:
        best_link = llm_client.chat(
            [
                {"role": "system", "content": "You are a helpful assistant that selects the best link. Output only the URL itself."},
                {"role": "user", "content": prompt}
            ],
            role="chat",
            temperature=0,
            reasoning="off",
        ).strip()
        # Verify the returned link is actually in our sources
        for s in sources:
            if s in best_link:
                return s
        return sources[0] # Fallback if LLM failed
    except Exception as e:
        print(f"Error determining best link: {e}")
        return sources[0]


@app.route('/chat', methods=['POST'])
@limiter.limit("20 per minute")
def chat_endpoint():
    data = request.json or {}
    session_id = data.get('session_id', 'unknown')
    user_message = data.get('message', '')
    bot_response = None
    # Wall-clock latency of the LLM call, in ms. Stays 0 for non-LLM branches
    # (greetings, canned deferrals) so every conversation-log row carries the
    # same metadata keys — old rows without it default to 0 on the dashboard.
    latency_ms = 0

    try:
        # Safety check
        if not full_database:
            print("[ERROR] full_database is None - database not loaded")
            bot_response = "Error: Database not initialized. Please try again in a moment or contact the administrator."
            return jsonify({"response": bot_response}), 503

        # Get message from Frontend
        user_query = data.get('message')
        history = data.get('history', [])
        language = data.get('language', 'English')

        if not user_query:
            return jsonify({"error": "No message provided"}), 400

        print(f"\n[Enrollment User Query]: {user_query}")

        # --- Spam Detection ---
        fingerprint = get_client_fingerprint()
        is_spam, spam_type = check_spam(fingerprint, user_query)
        if is_spam:
            print(f"[Spam Detected]: {spam_type} from {fingerprint}")
            bot_response = SPAM_RESPONSES[spam_type]
            return jsonify({"response": bot_response}), 200

        # --- GREETING HANDLING ---
        greetings = ["hello", "hi", "hey", "how are you", "how are you?"]
        response_text = ""
        relevant_chunks = []
        # Faithfulness is only audited for genuine, knowledge-base-grounded answers.
        # Greetings and canned deferrals are not answers about retrieved content, so
        # they stay False and are never scored (they were the false hallucinations).
        should_score = False

        if user_query.lower().strip() in greetings:
            response_text = GREETING_MESSAGE
            print(f"[AI Response]: {response_text}")
        else:
            # 0. Contextualize Query
            search_query = user_query
            if history:
                search_query = contextualize_query(history, user_query)
                print(f"[Rewritten Query]: {search_query}")

            # 1. Retrieve Context
            passage, metadatas = get_relevant_documents(search_query, full_database)

            if passage == "Error retrieving documents.":
                # Retrieval failure (embedding quota, DB hiccup, stale handle) —
                # return a clear retry message instead of letting the error string
                # reach the LLM prompt, where it reads as "no information available".
                print("[ERROR] Retrieval failed — returning 503 to client")
                bot_response = ("I'm having trouble accessing my knowledge base right now. "
                                "Please try again in a few minutes.")
                return jsonify({"response": bot_response}), 503

            if passage and passage != "No relevant information found.":
                relevant_chunks = passage.split("\n\n")

            # 2. Validation
            if passage == "No relevant information found.":
                response_text = DEFERRAL_MESSAGE
                print(f"[AI Response]: {response_text}")
            else:
                # 3. Construct Prompt (Specialized for enrollment agent)
                prompt = make_prompt(user_query, passage, history, language)
                # Maybe modify prompt slightly for enrollment context if needed,
                # but standard make_prompt works if passage is good.

                # 4. Generate Answer with OpenRouter
                t0 = time.perf_counter()
                response_text = llm_client.chat(
                    [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt}
                    ],
                    role="chat",
                    temperature=0.3,
                    reasoning="off",
                ).strip()
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)

                # Check for standard "no information" responses
                negative_phrases = [
                    "does not contain information",
                    "passage does not mention",
                    "provided passage does not",
                    "i don't have that information",
                    "cannot answer this question"
                ]

                if any(phrase in response_text.lower() for phrase in negative_phrases):
                     response_text = DEFERRAL_MESSAGE


                else:
                    # Genuine answer grounded in retrieved chunks — audit it.
                    should_score = True
                    # Add source link
                    if metadatas:
                        sources = list(set([m.get('source') for m in metadatas if m and m.get('source')]))
                        if sources:
                            best_link = get_best_link(user_query, response_text, sources)
                            if best_link:
                                response_text += f"\n\nSource: {best_link}"
                print(f"[Enrollment Answer]: {response_text}")

        # 5. Log Conversation
        if full_database_conversations:
            try:
                interaction_id = str(uuid.uuid4())
                timestamp = datetime.now().isoformat()
                log_entry = f"User: {user_query}\nAI: {response_text}"
                full_database_conversations.add(
                    documents=[log_entry],
                    metadatas=[{"role": "interaction", "timestamp": timestamp, "latency_ms": latency_ms}],
                    ids=[interaction_id]
                )


            except Exception as log_error:
                print(f"Error logging enrollment conversation: {log_error}")

        # Spawn faithfulness scoring on a daemon thread so the user is not blocked.
        # Only genuine answers grounded in retrieved chunks are audited; greetings
        # and canned deferrals are skipped. The judge is handed SITE_FACTS so
        # answers drawn from the system prompt's authoritative facts (hours,
        # locations, headline numbers) are not mis-flagged as hallucinations.
        if should_score:
            conversation_id = str(uuid.uuid4())
            threading.Thread(
                target=score_faithfulness_async,
                args=(user_query, relevant_chunks, response_text, conversation_id, SITE_FACTS),
                daemon=True,
            ).start()

        bot_response = response_text
        return jsonify({"response": bot_response})

    except Exception as e:
        bot_response = f"Internal error: {str(e)[:100]}"
        return jsonify({"response": bot_response}), 500

@app.route('/generate-title', methods=['POST'])
@limiter.limit("20 per minute")
def generate_title_endpoint():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    language = data.get("language") or "English"

    if not message:
        return jsonify({"error": "message required"}), 400
    if len(message) > 2000:
        return jsonify({"error": "message too long"}), 400

    try:
        title = generate_title(message, language)
    except Exception as e:
        print(f"[generate-title] LLM failed: {e}")
        title = _fallback_title(message)

    return jsonify({"title": title}), 200


# --- Guided workflows -------------------------------------------------------
# A workflow is a questionnaire the assistant walks a visitor through, defined
# by a JSON spec in workflows/. See agent_chatbot/workflow_engine.py.
#
# These are deliberately NOT part of /chat, for three concrete reasons:
#   * check_spam would fire on ordinary answers. is_gibberish("~40% / 60% (+-5)")
#     is 0.35 alphanumeric and reads as gibberish; and the duplicate rule blocks
#     the third identical "I don't know", which in an interview is normal.
#   * Retrieval and the faithfulness judge are meaningless here — a workflow
#     turn has no retrieved chunks to be faithful to.
#   * Workflow turns must stay out of full_database_conversations, which
#     dashboard_data reads unfiltered; bot-asked questions would skew every
#     metric, and each add() also costs an embedding call.
# Being separate routes means none of that is a conditional — it simply is not
# reached.

_workflow_registry_loaded = False


def _ensure_workflows():
    """Load specs on first use. Failure here must never break the server."""
    global _workflow_registry_loaded
    if not _workflow_registry_loaded:
        try:
            workflow_specs.load_all()
        except Exception as exc:
            print(f"[workflows] failed to load specs: {exc}")
        _workflow_registry_loaded = True
    return getattr(site_config_module, "WORKFLOWS_ENABLED", True)


def _workflow_or_error(workflow_id):
    """Resolve a workflow id to its registry entry, or an error response."""
    if not _ensure_workflows():
        return None, (jsonify({"error": "workflows_disabled"}), 503)
    entry = workflow_specs.get(workflow_id)
    if entry is None:
        return None, (jsonify({"error": "unknown_workflow"}), 404)
    return entry, None


@app.route('/api/workflows', methods=['GET'])
@limiter.limit("30 per minute")
def workflows_list():
    if not _ensure_workflows():
        return jsonify({"workflows": []}), 200
    return jsonify({"workflows": workflow_specs.summaries()}), 200


@app.route('/api/workflow/start', methods=['POST'])
@limiter.limit("10 per minute")
def workflow_start():
    data = request.get_json(silent=True) or {}
    entry, error = _workflow_or_error(data.get("workflow_id"))
    if error:
        return error
    language = data.get("language") or "English"
    try:
        # Zero LLM calls: opening a workflow is instant.
        return jsonify(workflow_engine.start(entry["spec"], entry["hash"], language)), 200
    except Exception as exc:
        print(f"[workflows] start failed: {exc}")
        return jsonify({"error": "workflow_error"}), 500


@app.route('/api/workflow/turn', methods=['POST'])
@limiter.limit("30 per minute")
def workflow_turn():
    data = request.get_json(silent=True) or {}
    entry, error = _workflow_or_error(data.get("workflow_id"))
    if error:
        return error

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message required"}), 400

    spec, spec_hash = entry["spec"], entry["hash"]
    try:
        state = workflow_engine.validate_state(spec, spec_hash, data.get("state"))
    except workflow_engine.WorkflowSpecChanged as changed:
        # The spec was edited mid-run. Keep the answers that still apply and
        # resume from the first unanswered question rather than losing the lot.
        migrated = changed.migrated_state
        payload = workflow_engine.resume_turn(
            spec, migrated, site_config_module.WORKFLOW_SPEC_CHANGED_MESSAGE)
        return jsonify(payload), 409
    except workflow_engine.WorkflowStateError as exc:
        reason = str(exc)
        status = 413 if reason == "state_too_large" else 400
        return jsonify({"error": reason}), status

    try:
        return jsonify(workflow_engine.handle_turn(spec, spec_hash, state, message)), 200
    except Exception as exc:
        print(f"[workflows] turn failed: {exc}")
        # Hand back the unchanged state so the run is recoverable rather than lost.
        return jsonify(workflow_engine.resume_turn(
            spec, state, site_config_module.WORKFLOW_ERROR_MESSAGE)), 200


@app.route('/api/workflow/document', methods=['POST'])
@limiter.limit("10 per minute")
def workflow_document():
    """Render the document from whatever has been answered so far.

    Deterministic and LLM-free, so it doubles as "give me what you have" mid-run
    and as a re-download after finishing.
    """
    data = request.get_json(silent=True) or {}
    entry, error = _workflow_or_error(data.get("workflow_id"))
    if error:
        return error
    try:
        state = workflow_engine.validate_state(entry["spec"], entry["hash"], data.get("state"))
    except workflow_engine.WorkflowSpecChanged as changed:
        state = changed.migrated_state
    except workflow_engine.WorkflowStateError as exc:
        reason = str(exc)
        return jsonify({"error": reason}), 413 if reason == "state_too_large" else 400
    return jsonify({"document": workflow_engine.render_document(entry["spec"], state)}), 200


def _responses_dir():
    configured = os.environ.get("WORKFLOW_RESPONSES_DIR") or getattr(
        site_config_module, "WORKFLOW_RESPONSES_DIR", "workflow_responses"
    )
    if os.path.isabs(configured):
        return configured
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, configured)


@app.route('/api/workflow/submit', methods=['POST'])
@limiter.limit("5 per hour")
def workflow_submit():
    """Save a completed response to disk, and email it when SMTP is configured.

    Tightly limited because it writes files and can send mail. There is
    deliberately no route that reads these back: responses can contain personal
    details and the dashboard API has no authentication.
    """
    data = request.get_json(silent=True) or {}
    entry, error = _workflow_or_error(data.get("workflow_id"))
    if error:
        return error

    spec = entry["spec"]
    try:
        state = workflow_engine.validate_state(spec, entry["hash"], data.get("state"))
    except workflow_engine.WorkflowSpecChanged as changed:
        state = changed.migrated_state
    except workflow_engine.WorkflowStateError as exc:
        reason = str(exc)
        return jsonify({"error": reason}), 413 if reason == "state_too_large" else 400

    document = workflow_engine.render_document(spec, state)
    saved = False
    try:
        directory = _responses_dir()
        os.makedirs(directory, exist_ok=True)
        # render_document already sanitized the filename to [A-Za-z0-9_-].md;
        # basename is a second belt against anything that slipped through.
        target = os.path.join(directory, os.path.basename(document["filename"]))
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(document["body"])
        saved = True
        print(f"[workflows] saved response: {target}")
    except OSError as exc:
        print(f"[workflows] could not save response: {exc}")

    emailed = False
    if data.get("email"):
        try:
            subject = site_config_module.WORKFLOW_EMAIL_SUBJECT.format(title=spec["title"])
            emailed = email_report.send_markdown_email(subject, document["body"], spec["title"])
        except Exception as exc:
            print(f"[workflows] could not email response: {exc}")

    return jsonify({"saved": saved, "emailed": emailed, "filename": document["filename"]}), 200


@app.route('/api/dashboard', methods=['GET'])
def dashboard_api():
    range_param = request.args.get('range', '24h')
    return jsonify(dashboard_data.compute_dashboard(full_database_conversations, range_param))


@app.route('/api/conversations', methods=['GET'])
def conversations_api():
    return jsonify(dashboard_data.compute_conversations(full_database_conversations))


@app.route('/api/events/<int:event_id>', methods=['GET'])
def event_detail_api(event_id):
    detail = dashboard_data.get_event_detail(full_database_conversations, event_id)
    if detail is None:
        return jsonify({"error": "Event not found"}), 404
    return jsonify(detail)


@app.route('/api/dashboard/sessions/<session_id>', methods=['GET'])
def session_detail_api(session_id):
    return jsonify(dashboard_data.get_session_detail(full_database_conversations, session_id))


@app.route('/api/dashboard/topics', methods=['GET'])
def dashboard_topics():
    return jsonify(dashboard_data.compute_topics(full_database_conversations))


@app.route('/api/dashboard/topics/<topic>/threads', methods=['GET'])
def dashboard_topic_threads(topic):
    return jsonify(dashboard_data.compute_topic_threads(full_database_conversations, topic))


@app.route('/api/dashboard/analysis', methods=['GET'])
def dashboard_analysis_api():
    """Insights (frustrated users, model comparison) derived from the same
    conversation logs that power the daily analysis report."""
    return jsonify(dashboard_data.compute_analysis(full_database_conversations))


@app.route('/dashboard', methods=['GET'])
def dashboard():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return send_from_directory(base_dir, 'dashboard.html')


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Server running on port {port}")
    # Bind to 0.0.0.0 so the port is reachable on cloud hosts
    app.run(host='0.0.0.0', port=port)

