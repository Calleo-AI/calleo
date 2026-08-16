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
@app.route('/chatbot.css', methods=['GET'])
def serve_chatbot_css():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css_path = os.path.join(base_dir, 'frontend', 'chatbot.css')
    if os.path.exists(css_path):
        return send_file(css_path)
    return "File not found", 404

@app.route('/chatbot.js', methods=['GET'])
def serve_chatbot_js():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base_dir, 'frontend', 'chatbot.js')
    if os.path.exists(js_path):
        return send_file(js_path)
    return "File not found", 404

@app.route('/site_config.js', methods=['GET'])
def serve_site_config_js():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base_dir, 'frontend', 'site_config.js')
    if os.path.exists(js_path):
        return send_file(js_path)
    return "File not found", 404

@app.route('/chat_history_store.js', methods=['GET'])
def serve_chat_history_store_js():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base_dir, 'frontend', 'chat_history_store.js')
    if os.path.exists(js_path):
        return send_file(js_path)
    return "File not found", 404

@app.route('/chatbot_iframe.html', methods=['GET'])
def serve_chatbot_iframe():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    iframe_path = os.path.join(base_dir, 'frontend', 'chatbot_iframe.html')
    if os.path.exists(iframe_path):
        return send_file(iframe_path)
    return "File not found", 404

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

