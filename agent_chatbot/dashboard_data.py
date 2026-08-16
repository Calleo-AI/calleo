"""Dashboard data layer.

Reads chat interactions from ChromaDB ``full_database_conversations`` — the
same collection the daily analysis report reads, written on every ``/chat``
request by server.py. Each log line is ``"User: ...\\nAI: ..."`` plus a
timestamp, so per-request latency and error tracebacks are not available.

Every loader returns a uniform list of interaction dicts::

    {id, timestamp(datetime), session_id, query, response, status,
     model, error_type}

so the payload builders below work against a single, consistent shape.
"""

import os
import re
import sys
import json
from datetime import datetime, timedelta

# llm_client (repo root) is the single source of truth for model ids;
# site_config is the single source of truth for the canned deferral text.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import llm_client
import site_config

DEFAULT_MODEL = llm_client.model_for("chat")
MODEL_DISPLAY_NAME = "Qwen3.5 397B"

# How large a gap (in minutes) between two consecutive interactions starts a new
# synthetic session, used only for the ChromaDB fallback (those logs have no
# session id of their own).
SESSION_GAP_MINUTES = 30

# Phrases the chatbot emits when it could not actually answer the user. These are
# the canned fallbacks produced by agent_chatbot/server.py plus a few generic
# "no information" markers — the same signals the analysis report flags as
# "unanswered questions".
FALLBACK_MARKERS = [
    # The server's deferral and this marker are the same constant by construction.
    site_config.DEFERRAL_MESSAGE.lower(),
    "i don't have that information",
    "does not contain information",
    "passage does not mention",
    "provided passage does not",
    "cannot answer this question",
    "internal error",
    "error: database not initialized",
]

# Keyword -> topic mapping for the FAQ heatmap and topic threads. First match
# wins, so order matters. This default list covers the topics a school or an
# NGO site tends to get asked about; trim or extend it for your deployment —
# an unmatched query just falls through to "Other".
TOPIC_KEYWORDS = [
    (r'\b(admission|apply|application)\b', 'Admissions'),
    (r'\b(enroll|register|registration)\b', 'Enrollment'),
    (r'\b(tuition|fee|cost|price|pay|payment|financial)\b', 'Tuition & Fees'),
    (r'\b(scholarship|bursary|aid)\b', 'Financial Aid'),
    (r'\b(curriculum|academic|course|class|subject|learn|program)\b', 'Academics'),
    (r'\b(sport|athletic|team|basketball|soccer|hockey|volleyball|tennis)\b', 'Athletics'),
    (r'\b(music|art|drama|theatre|theater|creative|band|choir)\b', 'Arts'),
    (r'\b(club|extracurricular|activity)\b', 'Clubs & Activities'),
    (r'\b(uniform|dress code)\b', 'Uniforms'),
    (r'\b(lunch|cafeteria|food|meal)\b', 'Lunch & Food'),
    (r'\b(bus|transportation|commute|shuttle)\b', 'Transportation'),
    (r'\b(visit|tour|open house)\b', 'Tours & Visits'),
    (r'\b(donate|donation|donor|fundrais\w*)\b', 'Donations'),
    (r'\b(volunteer\w*)\b', 'Volunteering'),
    (r'\b(eligib\w*|assistance|referral)\b', 'Services & Eligibility'),
    (r'\b(contact|phone|email|reach|call)\b', 'Contact Info'),
    (r'\b(location|address|map|direction)\b', 'Location'),
    (r'\b(schedule|hours|time|calendar|date|timetable)\b', 'Schedules'),
    (r'\b(technology|laptop|computer|ipad|device|tech)\b', 'Technology'),
    (r'\b(health|nurse|medical|covid|sick|wellness)\b', 'Health & Wellness'),
    (r'\b(safety|security|bully)\b', 'Safety'),
]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_doc(doc):
    """Split a ``"User: ...\\nAI: ..."`` log line into (query, response)."""
    if not doc:
        return "", ""
    parts = doc.split("\nAI: ", 1)
    if len(parts) == 2:
        query = parts[0].replace("User: ", "", 1).strip()
        response = parts[1].strip()
        return query, response
    return doc.replace("User: ", "", 1).strip(), ""


def _parse_timestamp(value):
    """Parse an ISO timestamp from metadata, falling back to ``now``."""
    if not value:
        return datetime.now()
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        # Tolerate a trailing 'Z' or other minor format drift.
        try:
            return datetime.fromisoformat(str(value).replace("Z", "").strip())
        except (ValueError, TypeError):
            return datetime.now()


def _is_fallback(response):
    if not response:
        return True
    low = response.lower()
    return any(marker in low for marker in FALLBACK_MARKERS)


def _classify_topic(text):
    if not text:
        return 'General'
    low = text.lower()
    for pattern, topic in TOPIC_KEYWORDS:
        if re.search(pattern, low):
            return topic
    return 'General'


# ---------------------------------------------------------------------------
# Source loaders
# ---------------------------------------------------------------------------

def _load_from_chroma(collection):
    """Load interactions from the ChromaDB conversations collection.

    Sessions are synthesised from time gaps (the logs carry no session id of
    their own).
    """
    if collection is None:
        return []

    try:
        data = collection.get()
    except Exception as e:  # pragma: no cover - defensive
        print(f"[dashboard_data] Failed to read ChromaDB collection: {e}")
        return []

    documents = data.get("documents") or []
    metadatas = data.get("metadatas") or []

    items = []
    for i, doc in enumerate(documents):
        meta = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
        query, response = _parse_doc(doc)
        items.append({
            "timestamp": _parse_timestamp(meta.get("timestamp")),
            "latency_ms": meta.get("latency_ms"),
            "query": query,
            "response": response,
        })

    items.sort(key=lambda x: x["timestamp"])

    # Assign stable ids and group into synthetic sessions by time gap.
    session_idx = -1
    last_ts = None
    for idx, item in enumerate(items):
        item["id"] = idx
        ts = item["timestamp"]
        if last_ts is None or (ts - last_ts) > timedelta(minutes=SESSION_GAP_MINUTES):
            session_idx += 1
        item["session_id"] = f"session-{session_idx:04d}"
        item["status"] = "error" if _is_fallback(item["response"]) else "success"
        item["model"] = DEFAULT_MODEL
        item["error_type"] = "Unanswered" if item["status"] == "error" else None
        last_ts = ts

    return items


def load_interactions(collection):
    """Load interactions from ChromaDB, sorted oldest -> newest."""
    return _load_from_chroma(collection)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _within_range(items, range_param):
    """Filter items to a rolling time window ('24h', '7d', '30d', 'all')."""
    deltas = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    if range_param not in deltas:
        return list(items)
    cutoff = datetime.now() - deltas[range_param]
    return [it for it in items if it["timestamp"] >= cutoff]


def _range_cutoff(range_param):
    """Return the datetime cutoff for a rolling window, or None for 'all'."""
    deltas = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    if range_param not in deltas:
        return None
    return datetime.now() - deltas[range_param]


def _load_faithfulness(range_param="all"):
    """Mean faithfulness score and unfaithful rate from the JSONL log written
    by agent_analysis/faithfulness_scorer.py (the same log the daily report's
    "Hallucination Audit" reads).

    Scoped to the rolling window. Returns zeros if the log is missing or empty
    so the dashboard degrades gracefully on a fresh install.
    """
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "agent_analysis", "faithfulness_log.jsonl",
    )
    if not os.path.exists(path):
        return {"mean_score": 0.0, "unfaithful_rate_pct": 0.0, "scored_count": 0}

    cutoff = _range_cutoff(range_param)
    scores = []
    unfaithful = 0
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Skip records outside the window when one is set.
            ts = _parse_timestamp(r.get("timestamp"))
            if cutoff and ts < cutoff:
                continue
            total += 1
            if isinstance(r.get("score"), (int, float)):
                scores.append(r["score"])
            if r.get("faithful") is False:
                unfaithful += 1

    mean = round(sum(scores) / len(scores), 2) if scores else 0.0
    rate = round(unfaithful / total * 100, 1) if total else 0.0
    return {"mean_score": mean, "unfaithful_rate_pct": rate, "scored_count": total}


def _preview(text, limit=200):
    return (text or "")[:limit]


# ---------------------------------------------------------------------------
# Endpoint payload builders
# ---------------------------------------------------------------------------

def compute_dashboard(collection, range_param="24h"):
    if range_param not in ("24h", "7d", "30d"):
        range_param = "24h"

    items = load_interactions(collection)
    scoped = _within_range(items, range_param)

    total_chats = len(scoped)
    error_count = sum(1 for it in scoped if it["status"] == "error")
    error_rate = round((error_count / total_chats * 100) if total_chats else 0, 1)
    unique_sessions = len({it["session_id"] for it in scoped})

    # Average LLM response latency (ms). Old conversation-log rows and non-LLM
    # branches (greetings, canned deferrals) carry no latency, so only rows
    # with a positive value count toward the average.
    latencies = [it["latency_ms"] for it in scoped
                 if isinstance(it.get("latency_ms"), (int, float)) and it["latency_ms"] > 0]
    avg_latency_ms = round(sum(latencies) / len(latencies), 0) if latencies else 0

    # Faithfulness / answer-quality from the JSONL audit log, scoped to window.
    faith = _load_faithfulness(range_param)

    # Recent events: newest first, capped at 50, across all data.
    recent = sorted(items, key=lambda x: x["timestamp"], reverse=True)[:50]
    recent_events = [
        {
            "id": it["id"],
            "timestamp": it["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": it["session_id"],
            "user_message_preview": _preview(it["query"]),
            "bot_response_preview": _preview(it["response"]),
            "status": it["status"],
            "error_type": it.get("error_type"),
            "model": it.get("model") or DEFAULT_MODEL,
        }
        for it in recent
    ]

    payload = {
        "kpis": {
            "total_chats": total_chats,
            "error_rate_pct": error_rate,
            "error_count": error_count,
            "unique_sessions": unique_sessions,
            "avg_latency_ms": avg_latency_ms,
            "mean_faithfulness": faith["mean_score"],
            "unfaithful_rate_pct": faith["unfaithful_rate_pct"],
            "scored_count": faith["scored_count"],
        },
        "recent_events": recent_events,
    }

    if range_param == "24h":
        payload["hourly_traffic"] = _hourly_traffic(scoped)
    elif range_param == "7d":
        payload["daily_traffic"] = _daily_traffic(scoped, 7)
    else:
        payload["daily_traffic"] = _daily_traffic(scoped, 30)

    return payload


def _hourly_traffic(scoped):
    """24 buckets, one per hour-of-day (00..23), counting items in the window."""
    buckets = [0] * 24
    for it in scoped:
        buckets[it["timestamp"].hour] += 1
    return [{"hour": h, "count": buckets[h]} for h in range(24)]


def _daily_traffic(scoped, days):
    """One bucket per calendar day for the trailing ``days`` days."""
    today = datetime.now().date()
    counts = {}
    for it in scoped:
        counts[it["timestamp"].date()] = counts.get(it["timestamp"].date(), 0) + 1

    out = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        total = counts.get(day, 0)
        if days <= 7:
            out.append({"day": day.strftime("%a"), "total": total})
        else:
            out.append({
                "label": day.strftime("%m/%d"),
                "total": total,
                "date": day.isoformat(),
            })
    return out


def compute_conversations(collection):
    """Per-session summaries for the Conversations table.

    Mirrors ``recent_events`` in ``compute_dashboard``: aggregates across the
    full dataset (no time-window scoping) so the table is populated whenever
    the Recent events table is, and returns the 50 most recently active
    threads — matching the card's "Last 50 active threads" label.
    """
    items = load_interactions(collection)
    summaries = _sessions_summary(items)
    return summaries[:50]


def _sessions_summary(scoped):
    by_session = {}
    for it in scoped:
        by_session.setdefault(it["session_id"], []).append(it)

    summaries = []
    for sid, msgs in by_session.items():
        msgs.sort(key=lambda x: x["timestamp"])
        has_error = any(m["status"] == "error" for m in msgs)
        last_active = max(m["timestamp"] for m in msgs)
        summaries.append({
            "session_id": sid,
            "message_count": len(msgs),
            "last_active": last_active.isoformat(),
            "topic_preview": msgs[0]["query"],
            "status": "error" if has_error else "success",
            "model": msgs[0].get("model") or DEFAULT_MODEL,
        })

    summaries.sort(key=lambda x: x["last_active"], reverse=True)
    return summaries


def compute_topics(collection):
    """FAQ heatmap: count of sessions per topic over the last 7 days."""
    items = load_interactions(collection)
    scoped = _within_range(items, "7d")

    # One topic per session, based on its first question.
    first_by_session = {}
    for it in sorted(scoped, key=lambda x: x["timestamp"]):
        first_by_session.setdefault(it["session_id"], it["query"])

    counts = {}
    for query in first_by_session.values():
        topic = _classify_topic(query)
        counts[topic] = counts.get(topic, 0) + 1

    sorted_topics = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:15]
    return [{"topic": t, "count": c} for t, c in sorted_topics]


def compute_topic_threads(collection, topic):
    """All sessions (last 7 days) whose first question matches ``topic``."""
    items = load_interactions(collection)
    scoped = _within_range(items, "7d")
    summaries = _sessions_summary(scoped)

    # Recompute first-question topic per session to filter.
    first_by_session = {}
    for it in sorted(scoped, key=lambda x: x["timestamp"]):
        first_by_session.setdefault(it["session_id"], it["query"])

    threads = []
    for s in summaries:
        if _classify_topic(first_by_session.get(s["session_id"], "")) != topic:
            continue
        threads.append({
            "session_id": s["session_id"],
            "sid": s["session_id"],
            "status": s["status"],
            "isError": s["status"] == "error",
            "message_count": s["message_count"],
            "messages": s["message_count"],
            "last_active": s["last_active"],
            "timeLabel": s["last_active"],
            "preview": s["topic_preview"],
            "topic_preview": s["topic_preview"],
        })
    return threads


def get_session_detail(collection, session_id):
    items = load_interactions(collection)
    msgs = [it for it in items if it["session_id"] == session_id]
    msgs.sort(key=lambda x: x["timestamp"])

    messages = []
    for it in msgs:
        ts = it["timestamp"].isoformat()
        messages.append({"role": "user", "content": it["query"], "timestamp": ts})
        messages.append({
            "role": "error" if it["status"] == "error" else "assistant",
            "content": it["response"],
            "timestamp": ts,
        })

    start_time = msgs[0]["timestamp"].isoformat() if msgs else ""
    model = msgs[0].get("model") if msgs else DEFAULT_MODEL
    return {
        "session_id": session_id,
        "start_time": start_time,
        "message_count": len(messages),
        "model": model or DEFAULT_MODEL,
        "messages": messages,
    }


def get_event_detail(collection, event_id):
    items = load_interactions(collection)
    for it in items:
        if it["id"] == event_id:
            is_error = it["status"] == "error"
            default_msg = "The chatbot could not answer this question from its knowledge base."
            return {
                "id": it["id"],
                "timestamp": it["timestamp"].isoformat(),
                "session_id": it["session_id"],
                "user_message_preview": _preview(it["query"]),
                "bot_response_preview": _preview(it["response"]),
                "status": it["status"],
                "error_type": it.get("error_type"),
                "error": it.get("error_message") or (default_msg if is_error else None),
                "error_message": it.get("error_message") or (default_msg if is_error else None),
                "model": it.get("model") or DEFAULT_MODEL,
                "full_user_message": it["query"],
                "full_bot_response": it["response"],
            }
    return None


def compute_analysis(collection):
    """Insights derived from the same conversation logs the daily report uses.

    Powers the Frustrated users and Model comparison sections.
    """
    items = load_interactions(collection)

    # ---- Frustrated users ----------------------------------------------
    # A session is "frustrated" if the user repeated a question or the bot
    # produced two or more unanswered/fallback responses.
    by_session = {}
    for it in items:
        by_session.setdefault(it["session_id"], []).append(it)

    frustrated_sessions = []
    for sid, msgs in by_session.items():
        fallbacks = sum(1 for m in msgs if m["status"] == "error")
        seen = {}
        repeated = False
        for m in msgs:
            key = (m["query"] or "").lower().strip()
            if not key:
                continue
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= 2:
                repeated = True
        if fallbacks >= 2 or repeated:
            frustrated_sessions.append(sid)

    frustrated = {
        "count": len(frustrated_sessions),
        "session_ids": sorted(frustrated_sessions),
    }

    # ---- Model comparison ----------------------------------------------
    responses = [it["response"] for it in items if it["response"]]
    total = len(items)
    errors = sum(1 for it in items if it["status"] == "error")
    avg_words = (
        round(sum(len(r.split()) for r in responses) / len(responses))
        if responses else 0
    )
    model_comparison = [{
        "model": MODEL_DISPLAY_NAME,
        "model_id": DEFAULT_MODEL,
        "requests": total,
        "error_rate_pct": round((errors / total * 100) if total else 0, 1),
        "avg_response_words": avg_words,
    }]

    return {
        "frustrated": frustrated,
        "model_comparison": model_comparison,
        "total_interactions": total,
    }
