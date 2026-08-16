"""
Demo evaluation script.

Sends realistic prospective-parent / executive demo questions to /chat,
classifies each response, and on failure dumps the ChromaDB chunks that
were retrieved so we can tell whether it's a missing page, failed crawl,
or retrieval mismatch.

Usage:
    python tests/demo_eval.py
"""

import os
import sys
import json
import time
import textwrap
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

# Make sure we can import chatbot.get_chroma_db for direct ChromaDB inspection.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "agent_chatbot"))
from chatbot import get_chroma_db  # noqa: E402

SERVER_URL = os.environ.get("CHATBOT_SERVER_URL", "http://localhost:5000/chat")

QUESTIONS = {
    "Admissions Process": [
        "How do I apply to Example School for my child?",
        "What grades does the school accept new students into?",
        "When is the application deadline and what does the assessment day involve?",
    ],
    "Tuition & Fees": [
        "How much is tuition at Example School?",
        "Do you offer financial aid or bursaries, and how do families apply?",
        "Are there additional fees beyond tuition I should budget for?",
    ],
    "Academics & Programs": [
        "What does the Upper School academic program look like?",
        "Does the school offer AP courses?",
        "How does the school approach character education?",
    ],
    "School Facts": [
        "How many students attend the school and what is the student-teacher ratio?",
        "Where is Example School located?",
        "When was the school founded?",
    ],
    "Co-Curriculars": [
        "What sports teams does the school have?",
        "What arts and music programs are available to students?",
        "Are there outdoor education or trip opportunities?",
    ],
    "Campus & Facilities": [
        "Tell me about the campus facilities.",
        "Does the school have athletic facilities like a gym, pool, or fields?",
    ],
    "Staff & Leadership": [
        "Who is the Head of School?",
        "Who leads the admissions office?",
    ],
    # --- Executive demo questions (prospective / new parent perspective) ---
    "Enrollment & Transition": [
        "My child is currently in Grade 4 at a public school — what does the transition process look like?",
        "Is there a waitlist, and how does priority work?",
        "Can we visit the campus or sit in on a class before applying?",
    ],
    "Financial Aid & Affordability": [
        "We're a middle-income family — is the school realistically affordable for us?",
        "How much financial aid does the school give out each year in total?",
        "Is tuition the same across all grades, or does it increase in Upper School?",
    ],
    "University & Post-Graduation": [
        "What percentage of graduates receive university offers?",
        "Does the school have a university counselling or guidance program?",
    ],
    "Student Life & Wellbeing": [
        "What does a typical day look like for a Grade 7 student?",
        "How does the school handle bullying or conflicts between students?",
        "Is there a mental health counsellor or psychologist on staff?",
        "What is the average class size?",
    ],
    "Diversity & Inclusion": [
        "How diverse is the student body?",
        "Does the school accept international students, and is there ESL support?",
        "My family speaks Mandarin at home — can you help me in Chinese?",
    ],
    "Technology & Innovation": [
        "Does the school have a robotics or coding program?",
        "What technology do students use in the classroom?",
    ],
    "Safety & Logistics": [
        "Is it a day school or does it offer boarding?",
        "What are the school hours and is there before- or after-school care?",
        "How does drop-off and pick-up work — is there parking or a bus service?",
    ],
    "Reputation & Results": [
        "How does the school compare to other independent schools in the area?",
        "What is the student-to-teacher ratio?",
    ],
}

# Phrases the server / prompt produce when it can't answer.
DEFLECTION_MARKERS = [
    "i specialize in information relevant to",
    "enrolment team would love to answer",
    "i don't have that specific information",
    "i don't have that information",
    "do you have a question about the school",
    "does not contain information",
    "passage does not mention",
]

# Things that look like nav/boilerplate garbage rather than a real answer.
NAV_MARKERS = [
    "skip to main content",
    "main menu",
    "primary navigation",
    "cookie policy",
    "©",
    "all rights reserved",
]


def strip_source(text: str) -> str:
    """Strip the trailing 'Source: <url>' the server appends."""
    if "\n\nSource:" in text:
        return text.split("\n\nSource:", 1)[0].strip()
    return text.strip()


def classify(response_text: str) -> tuple[str, str]:
    """
    Returns (status, reason) where status is one of:
      - 'pass'      : real, substantive answer
      - 'deflect'   : "I don't have that information" style response
      - 'garbage'   : looks like nav/boilerplate or empty
    """
    body = strip_source(response_text)
    low = body.lower()

    if not body or len(body) < 20:
        return "garbage", f"response too short ({len(body)} chars)"

    for marker in DEFLECTION_MARKERS:
        if marker in low:
            return "deflect", f"matched deflection marker: '{marker}'"

    nav_hits = sum(1 for m in NAV_MARKERS if m in low)
    if nav_hits >= 2:
        return "garbage", f"matched {nav_hits} nav markers"

    return "pass", "substantive answer"


def ask(question: str) -> str:
    payload = {"message": question, "history": [], "language": "English"}
    try:
        r = requests.post(SERVER_URL, json=payload, timeout=60)
        r.raise_for_status()
        return r.json().get("response", "")
    except Exception as e:
        return f"[REQUEST ERROR] {e}"


def dump_chunks(question: str, db, n: int = 5) -> None:
    """Run the same retrieval the server does, and print what came back."""
    try:
        result = db.query(query_texts=[question], n_results=n)
    except Exception as e:
        print(f"        ChromaDB query failed: {e}")
        return

    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0] if result.get("distances") else [None] * len(docs)

    if not docs:
        print("        ChromaDB returned NO chunks for this query.")
        return

    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        src = (meta or {}).get("source", "<no source>")
        ctype = (meta or {}).get("type", "handbook")
        snippet = " ".join(doc.split())[:400]
        dist_str = f"{dist:.4f}" if dist is not None else "n/a"
        print(f"        [{i}] dist={dist_str}  type={ctype}")
        print(f"            source: {src}")
        print(f"            text  : {snippet}{'...' if len(doc) > 400 else ''}")


def main() -> None:
    # Sanity check: server reachable?
    try:
        h = requests.get(SERVER_URL.replace("/chat", "/health"), timeout=5)
        print(f"Server health: {h.status_code} {h.text.strip()}")
    except Exception as e:
        print(f"FATAL: server not reachable at {SERVER_URL}: {e}")
        sys.exit(1)

    db = get_chroma_db("full_database")
    print(f"ChromaDB 'full_database' loaded: {db.count()} documents indexed.\n")

    results: dict[str, list[dict]] = defaultdict(list)
    failures: list[dict] = []

    for category, questions in QUESTIONS.items():
        print(f"=== {category} ===")
        for q in questions:
            print(f"  Q: {q}")
            resp = ask(q)
            status, reason = classify(resp)
            print(f"     -> {status.upper()}  ({reason})")
            preview = strip_source(resp).replace("\n", " ")
            print(f"     A: {preview[:200]}{'...' if len(preview) > 200 else ''}")
            results[category].append({"q": q, "status": status, "reason": reason, "resp": resp})
            if status != "pass":
                failures.append({"category": category, "q": q, "status": status, "reason": reason, "resp": resp})
            # tiny delay so we don't trip rate limits / spam detection
            time.sleep(0.5)
        print()

    # ---------- Summary ----------
    print("\n" + "=" * 70)
    print("PASS / FAIL REPORT")
    print("=" * 70)

    overall_pass = overall_total = 0
    for category, items in results.items():
        passed = sum(1 for x in items if x["status"] == "pass")
        total = len(items)
        overall_pass += passed
        overall_total += total
        marker = "OK " if passed == total else "!! "
        print(f"{marker}{category:25s}  {passed}/{total}")
        for item in items:
            sym = "  +" if item["status"] == "pass" else "  -"
            print(f"{sym} [{item['status']:7s}] {item['q']}")
    print("-" * 70)
    print(f"TOTAL: {overall_pass}/{overall_total} passed")

    # ---------- Failure deep-dives ----------
    if failures:
        print("\n" + "=" * 70)
        print("FAILURE CHUNK DUMPS  (what the retriever actually returned)")
        print("=" * 70)
        for f in failures:
            print(f"\n[{f['category']}]  Q: {f['q']}")
            print(f"   status : {f['status']}  ({f['reason']})")
            print(f"   answer : {strip_source(f['resp'])[:300]}")
            print(f"   --- top-5 retrieved chunks ---")
            dump_chunks(f["q"], db, n=5)
    else:
        print("\nNo failures — nothing to dump.")


if __name__ == "__main__":
    main()
