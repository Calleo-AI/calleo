"""Reset the chatbot's analytics state: conversation logs + hallucination counter.

This empties the ``full_database_conversations`` ChromaDB collection (the source
for the dashboard and the weekly analysis report) and clears
``faithfulness_log.jsonl`` (the hallucination audit counter). The knowledge base
(``full_database``) is never touched.

The collection is emptied in place rather than dropped, so a running server keeps
working without a restart. Requires the ``--yes`` flag to actually make changes.

Usage (on the VM, from the repo root):
    python agent_analysis/reset_logs.py            # dry run: report only
    python agent_analysis/reset_logs.py --yes      # actually reset
"""
import os
import sys

import chromadb
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("CHROMA_DB_PATH")
CONVERSATIONS_COLLECTION = "full_database_conversations"
FAITHFULNESS_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "faithfulness_log.jsonl"
)


def reset(apply_changes):
    # 1. Conversation log collection
    client = chromadb.PersistentClient(path=DB_PATH)
    collection = client.get_or_create_collection(name=CONVERSATIONS_COLLECTION)
    ids = collection.get()["ids"]
    print(f"Conversation logs: {len(ids)} interaction(s) in '{CONVERSATIONS_COLLECTION}'.")
    if apply_changes and ids:
        collection.delete(ids=ids)
        print(f"  -> deleted {len(ids)} interaction(s). Remaining: {collection.count()}.")

    # 2. Hallucination counter (faithfulness log)
    if os.path.exists(FAITHFULNESS_LOG):
        line_count = sum(1 for _ in open(FAITHFULNESS_LOG, "r", encoding="utf-8"))
        print(f"Hallucination log: {line_count} record(s) in '{FAITHFULNESS_LOG}'.")
        if apply_changes:
            # Truncate rather than remove so the scorer can keep appending.
            open(FAITHFULNESS_LOG, "w", encoding="utf-8").close()
            print("  -> cleared faithfulness log.")
    else:
        print(f"Hallucination log: '{FAITHFULNESS_LOG}' does not exist (nothing to clear).")

    if not apply_changes:
        print("\nDry run — no changes made. Re-run with --yes to reset.")
    else:
        print("\nReset complete.")


if __name__ == "__main__":
    if not DB_PATH:
        print("Error: CHROMA_DB_PATH is not set in the environment/.env.")
        sys.exit(1)
    reset(apply_changes="--yes" in sys.argv)
