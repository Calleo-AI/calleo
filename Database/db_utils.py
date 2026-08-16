"""
db_utils.py — Shared ChromaDB helpers.

Imported by: create_db.py, update_db.py, snapshot_db.py, query_chunks.py,
insert_content.py, delete_chunk.py
"""

import os
import sys

import chromadb
from dotenv import load_dotenv

# llm_client lives at the repo root — the single seam for all LLM/embedding calls.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import llm_client

load_dotenv()


def _chroma_path():
    return os.environ.get(
        "CHROMA_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db"),
    )


def get_chroma_client():
    return chromadb.PersistentClient(path=_chroma_path())


def get_chroma_db(name):
    print(f"[ChromaDB] Using database path: {_chroma_path()}")
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=name, embedding_function=llm_client.get_embedding_function()
    )


def delete_collection(name):
    """Delete a collection if it exists (no-op otherwise)."""
    try:
        get_chroma_client().delete_collection(name)
    except Exception:
        pass
