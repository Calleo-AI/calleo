import os
import sys
import json
import datetime

# llm_client lives at the repo root — the single seam for all LLM/embedding calls.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import llm_client

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faithfulness_log.jsonl")


def _has_retrieved_content(retrieved_chunks):
    """True only if at least one retrieved chunk carries non-whitespace text.

    Greetings ("hello", "hi"), off-topic questions, and canned deferrals retrieve
    nothing from the knowledge base. With no DB content there is nothing for the
    response to be faithful *to*, so such interactions are not knowledge-base
    answers and must not be audited — auditing them against empty context is what
    previously flagged hardcoded greetings as hallucinations.
    """
    return any(chunk and chunk.strip() for chunk in retrieved_chunks)


def score_faithfulness_async(query, retrieved_chunks, response_text, conversation_id, trusted_facts=""):
    # A response can only "not match the content pulled from the database" when
    # content was actually pulled. No retrieved chunks => greeting / off-topic /
    # deferral => skip silently so it never counts as a hallucination.
    if not _has_retrieved_content(retrieved_chunks):
        return

    joined_chunks = "\n---\n".join(retrieved_chunks)
    trusted_block = trusted_facts.strip() if trusted_facts else "(none provided)"

    judge_prompt = f"""You are a factual faithfulness auditor for a website assistant chatbot.

RETRIEVED CONTEXT (from the site's knowledge base):
{joined_chunks}

TRUSTED BACKGROUND FACTS (authoritative facts the assistant is always permitted to state, even when they are absent from the retrieved context):
{trusted_block}

CHATBOT RESPONSE:
{response_text}

USER QUERY:
{query}

A claim is UNFAITHFUL only if it states a specific fact (number, name, date, policy, price) that is supported by NEITHER the retrieved context NOR the trusted background facts above. Any claim that matches the trusted background facts is faithful. General helpful statements, greetings, and appropriate deferrals to staff are never unfaithful.

Respond ONLY with valid JSON and absolutely nothing else — no markdown, no backticks, no preamble:
{{"faithful": true or false, "score": 0.0 to 1.0 where 1.0 is fully faithful, "suspicious_claims": ["claim 1", "claim 2"] as an empty list if faithful}}"""

    try:
        raw = llm_client.chat(
            [{"role": "user", "content": judge_prompt}],
            role="judge",
        ).strip()
        verdict = json.loads(raw)
    except Exception as e:
        print(f"[faithfulness_scorer] Failed to score conversation {conversation_id}: {e}")
        return

    record = {
        "timestamp": datetime.datetime.now().isoformat(),
        "conversation_id": conversation_id,
        "query": query,
        "score": verdict.get("score"),
        "faithful": verdict.get("faithful"),
        "suspicious_claims": verdict.get("suspicious_claims", []),
        "response_snippet": response_text[:200],
    }

    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[faithfulness_scorer] Failed to write log for {conversation_id}: {e}")
