"""Tests for the one-time Google -> OpenRouter embedding-config migration.

Operates on a hand-built chroma.sqlite3 (just the `collections` table), so no
ChromaDB, network, or embedding calls are involved.
"""
import json
import os
import sqlite3
import sys

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", os.path.join(os.path.dirname(__file__), "_tmp_chroma"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import migrate_embedding_config as migrate

GOOGLE_EF = {
    "type": "known",
    "name": "google_generative_ai",
    "config": {
        "api_key_env_var": "CHROMA_GOOGLE_GENAI_API_KEY",
        "model_name": "gemini-embedding-001",
        "task_type": "RETRIEVAL_DOCUMENT",
    },
}
HNSW = {"vector_index": {"hnsw": {"space": "cosine"}}}


def make_db(tmp_path, collections):
    """Write a minimal chroma.sqlite3 with {name: embedding_function_config}."""
    db_file = os.path.join(tmp_path, "chroma.sqlite3")
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE collections (name TEXT, config_json_str TEXT)")
    for name, ef_config in collections.items():
        config = dict(HNSW)
        if ef_config is not None:
            config["embedding_function"] = ef_config
        con.execute("INSERT INTO collections VALUES (?, ?)", (name, json.dumps(config)))
    con.commit()
    con.close()
    return db_file


def stored_config(db_file, name):
    con = sqlite3.connect(db_file)
    row = con.execute(
        "SELECT config_json_str FROM collections WHERE name = ?", (name,)
    ).fetchone()
    con.close()
    return json.loads(row[0])


class TestPlanMigration:
    def test_flags_google_collections(self, tmp_path):
        db_file = make_db(str(tmp_path), {"full_database": GOOGLE_EF})
        changes = migrate.plan_migration(db_file)
        assert [(name, old) for name, old, _ in changes] == [
            ("full_database", "google_generative_ai")
        ]

    def test_ignores_already_migrated_collections(self, tmp_path):
        db_file = make_db(
            str(tmp_path),
            {"full_database": {"type": "known", "name": "openrouter", "config": {}}},
        )
        assert migrate.plan_migration(db_file) == []

    def test_ignores_collections_without_an_embedding_function(self, tmp_path):
        db_file = make_db(str(tmp_path), {"full_database": None})
        assert migrate.plan_migration(db_file) == []


class TestApplyMigration:
    def test_rewrites_only_the_embedding_function(self, tmp_path):
        db_file = make_db(
            str(tmp_path),
            {"full_database": GOOGLE_EF, "other": {"type": "known", "name": "openrouter", "config": {}}},
        )
        migrate.apply_migration(db_file, migrate.plan_migration(db_file))

        migrated = stored_config(db_file, "full_database")
        assert migrated["embedding_function"] == {
            "type": "known",
            "name": "openrouter",
            "config": {"model_name": "google/gemini-embedding-001"},
        }
        # The vector index config (and therefore the stored vectors) is untouched.
        assert migrated["vector_index"] == HNSW["vector_index"]
        assert stored_config(db_file, "other")["embedding_function"]["name"] == "openrouter"

    def test_is_idempotent(self, tmp_path):
        db_file = make_db(str(tmp_path), {"full_database": GOOGLE_EF})
        migrate.apply_migration(db_file, migrate.plan_migration(db_file))
        assert migrate.plan_migration(db_file) == []


class TestMain:
    def _run(self, monkeypatch, argv):
        monkeypatch.setattr(sys, "argv", ["migrate_embedding_config.py"] + argv)
        return migrate.main()

    def test_dry_run_leaves_the_db_alone(self, tmp_path, monkeypatch):
        db_file = make_db(str(tmp_path), {"full_database": GOOGLE_EF})
        assert self._run(monkeypatch, ["--db-path", str(tmp_path), "--dry-run"]) == 0
        assert stored_config(db_file, "full_database")["embedding_function"] == GOOGLE_EF

    def test_migrates_in_place(self, tmp_path, monkeypatch):
        db_file = make_db(str(tmp_path), {"full_database": GOOGLE_EF})
        assert self._run(monkeypatch, ["--db-path", str(tmp_path)]) == 0
        assert stored_config(db_file, "full_database")["embedding_function"]["name"] == "openrouter"

    def test_missing_database_reports_failure(self, tmp_path, monkeypatch):
        assert self._run(monkeypatch, ["--db-path", str(tmp_path)]) == 1
