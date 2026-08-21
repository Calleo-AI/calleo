"""
migrate_embedding_config.py — one-time migration off the Google embedding provider.

Collections built before the OpenRouter migration have the Google Generative AI
embedding function baked into their persisted ChromaDB configuration. Opening
one with the current embedding function fails outright:

    ValueError: An embedding function already exists in the collection
    configuration ... new: openrouter vs persisted: google_generative_ai

The stored vectors themselves are still fine — they came from the same
gemini-embedding-001 model we now reach through OpenRouter — so this script
rewrites the persisted embedding-function config in place instead of
re-embedding anything. Run it once per ChromaDB directory after upgrading.

Usage:
    python Database/migrate_embedding_config.py
    python Database/migrate_embedding_config.py --dry-run
    python Database/migrate_embedding_config.py --db-path /path/to/chroma_db
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_client
from db_utils import chroma_path

# Every embedding-function name this project could have persisted via Google.
GOOGLE_EF_NAMES = {"google_generative_ai", "google_palm", "google_vertex"}


def target_ef_config():
    """The embedding-function config the current llm_client would persist."""
    ef = llm_client.get_embedding_function()
    return {"type": "known", "name": ef.name(), "config": ef.get_config()}


def plan_migration(db_file):
    """Return [(collection_name, old_ef_name, new_config_json)] for Google collections."""
    changes = []
    con = sqlite3.connect(db_file)
    try:
        rows = list(con.execute("SELECT name, config_json_str FROM collections"))
    finally:
        con.close()

    for name, config_json in rows:
        try:
            config = json.loads(config_json) if config_json else {}
        except json.JSONDecodeError:
            print(f"[SKIP] {name}: configuration is not valid JSON")
            continue
        ef_config = config.get("embedding_function") or {}
        old_name = ef_config.get("name")
        if old_name not in GOOGLE_EF_NAMES:
            continue
        config["embedding_function"] = target_ef_config()
        changes.append((name, old_name, json.dumps(config)))
    return changes


def apply_migration(db_file, changes):
    con = sqlite3.connect(db_file)
    try:
        for name, _, new_config_json in changes:
            con.execute(
                "UPDATE collections SET config_json_str = ? WHERE name = ?",
                (new_config_json, name),
            )
        con.commit()
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", help="ChromaDB directory (default: CHROMA_DB_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    db_dir = args.db_path or chroma_path()
    db_file = os.path.join(db_dir, "chroma.sqlite3")
    print(f"[ChromaDB] {db_file}")
    if not os.path.isfile(db_file):
        print("[ERROR] No chroma.sqlite3 there — nothing to migrate.")
        return 1

    changes = plan_migration(db_file)
    if not changes:
        print("[OK] No collections use a Google embedding function. Nothing to do.")
        return 0

    new_name = target_ef_config()["name"]
    for name, old_name, _ in changes:
        print(f"  {name}: {old_name} -> {new_name}")
    if args.dry_run:
        print(f"[DRY RUN] {len(changes)} collection(s) would be migrated.")
        return 0

    apply_migration(db_file, changes)
    print(f"[OK] Migrated {len(changes)} collection(s). Stored vectors were left as-is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
