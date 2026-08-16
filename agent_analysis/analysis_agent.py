import chromadb
import os
import sys
import json
import datetime
from dotenv import load_dotenv

# llm_client lives at the repo root — the single seam for all LLM/embedding calls.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import llm_client

load_dotenv()

# Configuration
DB_PATH = os.environ.get("CHROMA_DB_PATH")
COLLECTION_NAME = "full_database_conversations"
MODEL_NAME = llm_client.model_for("analysis")  # for log output only

# The report is emailed weekly and covers only the trailing 7 days of activity,
# so both the conversation logs and the faithfulness audit are filtered to this
# window. Interactions logged before the window (or without a timestamp) are
# excluded from the report.
REPORT_WINDOW_DAYS = 7

# Faithfulness scores run 0.0 (fully unfaithful) to 1.0 (fully faithful). A
# response flagged unfaithful is only detailed — with its suspicious claims — in
# the report when its score is at or below this cutoff, so borderline flags
# don't surface low-confidence "suspicious claims" to readers.
SUSPICIOUS_CLAIM_SCORE_THRESHOLD = 0.3

def get_chroma_collection():
    """Connects to the ChromaDB collection."""
    try:
        chroma_client = chromadb.PersistentClient(path=DB_PATH)
        collection = chroma_client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=llm_client.get_embedding_function(),
        )
        return collection
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

def parse_conversations(documents):
    """Parses raw log strings into structured data."""
    parsed_data = []
    for doc in documents:
        try:
            # Expected format: "User: [query]\nAI: [response]"
            parts = doc.split("\nAI: ", 1)
            if len(parts) == 2:
                user_query = parts[0].replace("User: ", "").strip()
                ai_response = parts[1].strip()
                parsed_data.append({"query": user_query, "response": ai_response})
        except Exception:
            continue
    return parsed_data

def analyze_with_openrouter(conversations):
    """Sends the conversation data to OpenRouter for analysis."""
    if not conversations:
        return "No conversations found to analyze."

    # Prepare the data for the prompt
    conversation_text = ""
    for i, conv in enumerate(conversations, 1):
        conversation_text += f"Interaction {i}:\nUser: {conv['query']}\nAI: {conv['response']}\n\n"

    prompt = f"""
    You are an expert data analyst for a website chatbot.
    Analyze the following conversation logs and generate a report.

    DATA:
    {conversation_text}

    YOUR TASK:
    1. **Identify Trends**: What are the top 3-5 most frequent topics or questions asked?
    2. **Unanswered Questions**: Identify questions where the AI failed to provide a helpful answer (e.g., responded with "I don't have that information" or similar).
    3. **Content Gaps**: Based on the unanswered questions, what specific information should be added to the handbook/database?
    4. **Recommendations**: Suggest any improvements for the chatbot's responses or what information needs to be added to the website in order to produce a satisfying answer to the user's query.

    OUTPUT FORMAT:
    Produce a clean Markdown report covering the four sections above. Do NOT include a date header or interaction count — those are prepended automatically. Begin directly with the analysis (e.g., a "## 1. Trends" heading).
    """

    try:
        return llm_client.chat(
            [
                {"role": "system", "content": "You are a helpful data analyst. Always respond in English regardless of the language of the input data."},
                {"role": "user", "content": prompt}
            ],
            role="analysis",
            reasoning="high",
        )
    except Exception as e:
        return f"Error during AI analysis: {e}"

def build_hallucination_section(log_path=None, since=None):
    """Read faithfulness_log.jsonl and return a markdown 'Hallucination Audit' section.

    Only records with a timestamp on or after ``since`` (a datetime) are counted,
    so the audit covers the same trailing window as the rest of the weekly report.
    When ``since`` is None all records are included.

    Suspicious claims are only detailed for responses whose faithfulness score is
    at or below SUSPICIOUS_CLAIM_SCORE_THRESHOLD; higher-scoring flagged responses
    are omitted from the detail list (but still counted in the top-line summary).

    Returns None if the log is missing or empty so the caller can skip emitting the section.
    """
    if log_path is None:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faithfulness_log.jsonl")
    if not os.path.exists(log_path):
        return None

    records = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is not None:
                ts_raw = record.get("timestamp")
                if not ts_raw:
                    continue  # undated records predate windowing; exclude from the weekly audit
                try:
                    if datetime.datetime.fromisoformat(ts_raw) < since:
                        continue
                except ValueError:
                    continue
            records.append(record)

    if not records:
        return None

    total = len(records)
    scores = [r["score"] for r in records if isinstance(r.get("score"), (int, float))]
    mean_score = sum(scores) / len(scores) if scores else 0.0
    unfaithful = [r for r in records if r.get("faithful") is False]
    unfaithful_count = len(unfaithful)
    rate = (unfaithful_count / total * 100) if total else 0.0

    lines = [
        "## 5. Hallucination Audit",
        "",
        f"Automated faithfulness scoring (DeepSeek v4 Flash judge) ran on **{total}** chatbot responses over the past {REPORT_WINDOW_DAYS} days.",
        "",
        f"- **Mean faithfulness score:** {mean_score:.2f} / 1.00",
        f"- **Responses flagged as unfaithful:** {unfaithful_count} ({rate:.1f}%)",
        "",
    ]

    # Only detail flagged responses whose score is very low; a flag alone is not
    # enough to surface its (potentially low-confidence) suspicious claims.
    flagged_detail = [
        r for r in unfaithful
        if isinstance(r.get("score"), (int, float))
        and r["score"] <= SUSPICIOUS_CLAIM_SCORE_THRESHOLD
    ]

    if flagged_detail:
        lines.append(f"### Flagged Responses (faithfulness score <= {SUSPICIOUS_CLAIM_SCORE_THRESHOLD:.1f})")
        lines.append("")
        for r in flagged_detail:
            ts = r.get("timestamp", "?")
            query = (r.get("query") or "").replace("\n", " ").strip()
            score = r.get("score", "?")
            claims = r.get("suspicious_claims") or []
            lines.append(f"- **{ts}** — score {score} — query: _{query}_")
            for c in claims:
                lines.append(f"    - Suspicious claim: {c}")
        lines.append("")
    elif unfaithful:
        lines.append(
            f"{unfaithful_count} response(s) were flagged as unfaithful, but none scored "
            f"at or below {SUSPICIOUS_CLAIM_SCORE_THRESHOLD:.1f}; suspicious claims omitted as low-confidence."
        )
        lines.append("")
    else:
        lines.append("No responses were flagged as unfaithful in the audited window.")
        lines.append("")

    return "\n".join(lines)


def main():
    print("--- Starting Conversation Analysis ---")
    
    # 1. Fetch Data
    collection = get_chroma_collection()
    if not collection:
        return

    print("Fetching conversation logs...")
    all_docs = collection.get(include=["documents", "metadatas"])
    documents = all_docs['documents']
    metadatas = all_docs['metadatas'] or []

    if not documents:
        print("No conversations found in the database.")
        return

    # Keep only interactions logged within the trailing report window. Rows are
    # filtered on their `timestamp` metadata (ISO-8601, written by server.py);
    # rows without a timestamp predate windowing and are excluded.
    now = datetime.datetime.now()
    window_start = now - datetime.timedelta(days=REPORT_WINDOW_DAYS)
    windowed = []
    for doc, meta in zip(documents, metadatas):
        ts_raw = (meta or {}).get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts >= window_start:
            windowed.append(doc)
    documents = windowed

    if not documents:
        print(f"No conversations logged in the past {REPORT_WINDOW_DAYS} days; nothing to report.")
        return

    interaction_count = len(documents)
    print(f"Found {interaction_count} interactions in the past {REPORT_WINDOW_DAYS} days.")

    # 2. Parse Data
    parsed_conversations = parse_conversations(documents)

    # 3. Analyze
    print(f"Analyzing with OpenRouter ({MODEL_NAME})...")
    analysis_body = analyze_with_openrouter(parsed_conversations)

    # 3a. Prepend deterministic header (reporting period + interaction count)
    period_start = window_start.strftime("%B %d, %Y")
    period_end = now.strftime("%B %d, %Y")
    header = (
        f"# Weekly Conversation Analysis Report\n\n"
        f"**Reporting period:** {period_start} – {period_end} (past {REPORT_WINDOW_DAYS} days)\n\n"
        f"**Interactions logged this period:** {interaction_count}\n\n"
        f"---\n\n"
    )
    report = header + analysis_body.lstrip()

    # 3b. Append deterministic hallucination audit from faithfulness_log.jsonl,
    #     scoped to the same trailing window as the conversation analysis.
    hallucination_section = build_hallucination_section(since=window_start)
    if hallucination_section:
        report = report.rstrip() + "\n\n---\n\n" + hallucination_section
        print("Hallucination audit section appended.")
    else:
        print("No faithfulness log found; skipping hallucination audit section.")

    # 3c. Append AI-generated disclaimer
    disclaimer = (
        "---\n\n"
        "_Disclaimer: This report was generated by AI and may contain inaccuracies. "
        "Please verify findings before acting on them._\n"
    )
    report = report.rstrip() + "\n\n" + disclaimer

    # 4. Output
    print("\n--- Analysis Report ---\n")
    print(report)

    current_date_str = datetime.datetime.now().strftime("%Y_%m_%d")
    report_filename = f"{current_date_str}_analysis_report.md"
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(script_dir, "analysis_reports")
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, report_filename)
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport saved to '{report_path}'")

if __name__ == "__main__":
    main()
