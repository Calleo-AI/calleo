"""
Unit tests for Database/snapshot_db.py

All filesystem operations use pytest's tmp_path fixture.
ChromaDB and database imports are stubbed before import.
"""

import os
import sys
import json
from unittest.mock import MagicMock, patch

import pytest

# ------------------------------------------------------------------
# Stub heavy external dependencies before importing snapshot_db.
# ------------------------------------------------------------------

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", "/tmp/test-chroma")

for _mod in ("database", "chromadb", "dotenv"):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["database"].get_chroma_db = MagicMock()
sys.modules["chromadb"].PersistentClient = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))
import snapshot_db  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_snap(directory, timestamp, doc_count=10):
    """Write a minimal valid snapshot file to directory."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{timestamp}_snap.json")
    data = {
        "collection": "col",
        "created_at": timestamp,
        "document_count": doc_count,
        "ids": [f"id_{i}" for i in range(doc_count)],
        "documents": [f"doc_{i}" for i in range(doc_count)],
        "metadatas": [{"source": "https://x.com"}] * doc_count,
        "embeddings": [[0.1, 0.2]] * doc_count,
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return path


# ---------------------------------------------------------------------------
# list_snapshots
# ---------------------------------------------------------------------------

class TestListSnapshots:
    def test_returns_empty_list_when_directory_missing(self, tmp_path):
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            result = snapshot_db.list_snapshots("no_such_collection")
        assert result == []

    def test_returns_empty_list_when_no_snapshot_files(self, tmp_path):
        col_dir = tmp_path / "my_col"
        col_dir.mkdir()
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            result = snapshot_db.list_snapshots("my_col")
        assert result == []

    def test_returns_one_snapshot(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T14-05-00", doc_count=5)
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            result = snapshot_db.list_snapshots("my_col")
        assert len(result) == 1
        assert result[0]["timestamp"] == "2026-03-29T14-05-00"
        assert result[0]["document_count"] == 5

    def test_returns_snapshots_newest_first(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T09-00-00")
        _write_snap(str(col_dir), "2026-03-29T14-05-00")
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            result = snapshot_db.list_snapshots("my_col")
        assert result[0]["timestamp"] == "2026-03-29T14-05-00"
        assert result[1]["timestamp"] == "2026-03-29T09-00-00"

    def test_each_entry_has_required_keys(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00")
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            result = snapshot_db.list_snapshots("my_col")
        snap = result[0]
        assert "timestamp" in snap
        assert "path" in snap
        assert "document_count" in snap


# ---------------------------------------------------------------------------
# prune_snapshots
# ---------------------------------------------------------------------------

class TestPruneSnapshots:
    def test_returns_zero_when_directory_missing(self, tmp_path):
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            count = snapshot_db.prune_snapshots("missing_col", max_keep=5)
        assert count == 0

    def test_returns_zero_when_under_limit(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00")
        _write_snap(str(col_dir), "2026-03-29T11-00-00")
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            count = snapshot_db.prune_snapshots("my_col", max_keep=5)
        assert count == 0

    def test_deletes_oldest_when_over_limit(self, tmp_path):
        col_dir = tmp_path / "my_col"
        timestamps = [
            "2026-03-29T09-00-00",
            "2026-03-29T10-00-00",
            "2026-03-29T11-00-00",
            "2026-03-29T12-00-00",
            "2026-03-29T13-00-00",
            "2026-03-29T14-00-00",  # 6 total, keep 5 → delete oldest
        ]
        for ts in timestamps:
            _write_snap(str(col_dir), ts)
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            count = snapshot_db.prune_snapshots("my_col", max_keep=5)
        assert count == 1
        remaining = sorted(os.listdir(str(col_dir)))
        assert "2026-03-29T09-00-00_snap.json" not in remaining

    def test_returns_count_of_deleted_files(self, tmp_path):
        col_dir = tmp_path / "my_col"
        for i in range(8):
            _write_snap(str(col_dir), f"2026-03-29T{i:02d}-00-00")
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            count = snapshot_db.prune_snapshots("my_col", max_keep=5)
        assert count == 3

    def test_keeps_newest_files(self, tmp_path):
        col_dir = tmp_path / "my_col"
        timestamps = [f"2026-03-29T{i:02d}-00-00" for i in range(7)]
        for ts in timestamps:
            _write_snap(str(col_dir), ts)
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            snapshot_db.prune_snapshots("my_col", max_keep=5)
        remaining = os.listdir(str(col_dir))
        assert "2026-03-29T06-00-00_snap.json" in remaining
        assert "2026-03-29T05-00-00_snap.json" in remaining

    def test_max_keep_zero_deletes_all(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00")
        _write_snap(str(col_dir), "2026-03-29T11-00-00")
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            count = snapshot_db.prune_snapshots("my_col", max_keep=0)
        assert count == 2
        assert os.listdir(str(col_dir)) == []


# ---------------------------------------------------------------------------
# create_snapshot
# ---------------------------------------------------------------------------

class TestCreateSnapshot:
    def _make_collection(self, ids, documents, metadatas, embeddings):
        col = MagicMock()
        col.get.return_value = {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
            "embeddings": embeddings,
        }
        return col

    def test_creates_collection_directory(self, tmp_path):
        col = self._make_collection(["id0"], ["doc0"], [{"source": "x"}], [[0.1]])
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            snapshot_db.create_snapshot(col, "new_col")
        assert os.path.isdir(str(tmp_path / "new_col"))

    def test_writes_json_file(self, tmp_path):
        col = self._make_collection(["id0"], ["doc0"], [{"source": "x"}], [[0.1]])
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            ts = snapshot_db.create_snapshot(col, "my_col")
        snap_file = tmp_path / "my_col" / f"{ts}_snap.json"
        assert snap_file.exists()

    def test_snapshot_contains_correct_fields(self, tmp_path):
        ids = ["id0", "id1"]
        docs = ["doc0", "doc1"]
        metas = [{"source": "https://a.com"}, {"source": "https://b.com"}]
        embeds = [[0.1, 0.2], [0.3, 0.4]]
        col = self._make_collection(ids, docs, metas, embeds)
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            ts = snapshot_db.create_snapshot(col, "my_col")
        with open(str(tmp_path / "my_col" / f"{ts}_snap.json")) as f:
            data = json.load(f)
        assert data["collection"] == "my_col"
        assert data["document_count"] == 2
        assert data["ids"] == ids
        assert data["documents"] == docs
        assert data["metadatas"] == metas
        assert data["embeddings"] == embeds

    def test_returns_timestamp_string(self, tmp_path):
        col = self._make_collection([], [], [], [])
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            ts = snapshot_db.create_snapshot(col, "my_col")
        assert isinstance(ts, str)
        assert len(ts) > 0

    def test_calls_collection_get_with_correct_includes(self, tmp_path):
        col = self._make_collection([], [], [], [])
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            snapshot_db.create_snapshot(col, "my_col")
        col.get.assert_called_once_with(
            include=["documents", "embeddings", "metadatas"]
        )

    def test_prunes_after_writing(self, tmp_path):
        col_dir = tmp_path / "my_col"
        for i in range(5):
            _write_snap(str(col_dir), f"2026-03-28T{i:02d}-00-00")
        col = self._make_collection(["id0"], ["doc0"], [{"source": "x"}], [[0.1]])
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            snapshot_db.create_snapshot(col, "my_col")
        # 5 existing + 1 new = 6; prune to 5 → 1 deleted
        remaining = [f for f in os.listdir(str(col_dir)) if f.endswith("_snap.json")]
        assert len(remaining) == 5

    def test_empty_collection_snapshot_has_zero_count(self, tmp_path):
        col = self._make_collection([], [], [], [])
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            ts = snapshot_db.create_snapshot(col, "my_col")
        with open(str(tmp_path / "my_col" / f"{ts}_snap.json")) as f:
            data = json.load(f)
        assert data["document_count"] == 0

    def test_numpy_like_embeddings_serialized_as_lists(self, tmp_path):
        """Embeddings from ChromaDB may be numpy arrays; they must be JSON-serializable."""
        class FakeArray:
            def __init__(self, vals):
                self._vals = vals
            def tolist(self):
                return self._vals

        col = self._make_collection(
            ["id0"], ["doc0"], [{"source": "x"}],
            [FakeArray([0.1, 0.2])]
        )
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            ts = snapshot_db.create_snapshot(col, "my_col")
        with open(str(tmp_path / "my_col" / f"{ts}_snap.json")) as f:
            data = json.load(f)
        assert data["embeddings"] == [[0.1, 0.2]]


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

class TestRollback:
    def test_exits_with_error_when_no_snapshots(self, tmp_path):
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            with pytest.raises(SystemExit) as exc:
                snapshot_db.rollback("empty_col")
        assert exc.value.code == 1

    def test_exits_with_error_when_snapshot_id_not_found(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00")
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)):
            with pytest.raises(SystemExit) as exc:
                snapshot_db.rollback("my_col", snapshot_id="9999-01-01T00-00-00")
        assert exc.value.code == 1

    def test_deletes_existing_collection(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00", doc_count=2)
        mock_client = MagicMock()
        mock_new_col = MagicMock()
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)), \
             patch.object(snapshot_db.chromadb, "PersistentClient", return_value=mock_client), \
             patch.object(snapshot_db, "get_chroma_db", return_value=mock_new_col):
            snapshot_db.rollback("my_col")
        mock_client.delete_collection.assert_called_once_with("my_col")

    def test_recreates_collection_via_get_chroma_db(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00", doc_count=2)
        mock_client = MagicMock()
        mock_new_col = MagicMock()
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)), \
             patch.object(snapshot_db.chromadb, "PersistentClient", return_value=mock_client), \
             patch.object(snapshot_db, "get_chroma_db", return_value=mock_new_col) as mock_get:
            snapshot_db.rollback("my_col")
        mock_get.assert_called_once_with("my_col")

    def test_restores_documents_to_new_collection(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00", doc_count=2)
        mock_client = MagicMock()
        mock_new_col = MagicMock()
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)), \
             patch.object(snapshot_db.chromadb, "PersistentClient", return_value=mock_client), \
             patch.object(snapshot_db, "get_chroma_db", return_value=mock_new_col):
            snapshot_db.rollback("my_col")
        mock_new_col.add.assert_called()
        call_kwargs = mock_new_col.add.call_args_list[0][1]
        assert "ids" in call_kwargs
        assert "documents" in call_kwargs
        assert "metadatas" in call_kwargs
        assert "embeddings" in call_kwargs

    def test_uses_most_recent_snapshot_by_default(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T09-00-00", doc_count=10)
        _write_snap(str(col_dir), "2026-03-29T14-00-00", doc_count=20)
        mock_client = MagicMock()
        mock_new_col = MagicMock()
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)), \
             patch.object(snapshot_db.chromadb, "PersistentClient", return_value=mock_client), \
             patch.object(snapshot_db, "get_chroma_db", return_value=mock_new_col):
            snapshot_db.rollback("my_col")
        # The most recent snapshot has doc_count=20, so add is called with 20 ids
        all_ids = []
        for call in mock_new_col.add.call_args_list:
            all_ids.extend(call[1]["ids"])
        assert len(all_ids) == 20

    def test_uses_specified_snapshot_id(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T09-00-00", doc_count=10)
        _write_snap(str(col_dir), "2026-03-29T14-00-00", doc_count=20)
        mock_client = MagicMock()
        mock_new_col = MagicMock()
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)), \
             patch.object(snapshot_db.chromadb, "PersistentClient", return_value=mock_client), \
             patch.object(snapshot_db, "get_chroma_db", return_value=mock_new_col):
            snapshot_db.rollback("my_col", snapshot_id="2026-03-29T09-00-00")
        all_ids = []
        for call in mock_new_col.add.call_args_list:
            all_ids.extend(call[1]["ids"])
        assert len(all_ids) == 10

    def test_add_called_in_batches_of_100(self, tmp_path):
        col_dir = tmp_path / "my_col"
        _write_snap(str(col_dir), "2026-03-29T10-00-00", doc_count=250)
        mock_client = MagicMock()
        mock_new_col = MagicMock()
        with patch.object(snapshot_db, "SNAPSHOTS_DIR", str(tmp_path)), \
             patch.object(snapshot_db.chromadb, "PersistentClient", return_value=mock_client), \
             patch.object(snapshot_db, "get_chroma_db", return_value=mock_new_col):
            snapshot_db.rollback("my_col")
        # 250 docs → 3 batches (100, 100, 50)
        assert mock_new_col.add.call_count == 3


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_collection_is_required(self):
        with pytest.raises(SystemExit) as exc:
            snapshot_db.parse_args([])
        assert exc.value.code == 2

    def test_collection_flag_sets_name(self):
        args = snapshot_db.parse_args(["--collection", "full_database"])
        assert args.collection == "full_database"

    def test_default_no_list_no_rollback(self):
        args = snapshot_db.parse_args(["--collection", "full_database"])
        assert args.list is False
        assert args.rollback is False

    def test_list_flag(self):
        args = snapshot_db.parse_args(["--collection", "full_database", "--list"])
        assert args.list is True

    def test_rollback_flag(self):
        args = snapshot_db.parse_args(["--collection", "full_database", "--rollback"])
        assert args.rollback is True

    def test_snapshot_flag(self):
        args = snapshot_db.parse_args([
            "--collection", "full_database",
            "--rollback",
            "--snapshot", "2026-03-29T10-00-00",
        ])
        assert args.snapshot == "2026-03-29T10-00-00"

    def test_snapshot_defaults_to_none(self):
        args = snapshot_db.parse_args(["--collection", "full_database", "--rollback"])
        assert args.snapshot is None

    def test_list_and_rollback_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as exc:
            snapshot_db.parse_args(["--collection", "full_database", "--list", "--rollback"])
        assert exc.value.code == 2
