"""Tests for create_db.py validation gate and staging->live swap (no network/embedding)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", os.path.join(os.path.dirname(__file__), "_tmp_chroma"))

from create_db import validate, swap_into_live, KEY_PAGE_CHECKS, MIN_TOTAL_CHUNKS


class FakeCollection:
    """Minimal stand-in for a chromadb collection."""

    def __init__(self):
        self.rows = {}  # id -> {"document", "metadata", "embedding"}

    def add(self, ids, documents=None, metadatas=None, embeddings=None):
        for i, cid in enumerate(ids):
            self.rows[cid] = {
                "document": documents[i] if documents else None,
                "metadata": metadatas[i] if metadatas else {},
                "embedding": embeddings[i] if embeddings is not None else None,
            }

    def get(self, include=None, where=None):
        ids = list(self.rows)
        if where and "source" in where:
            ids = [i for i in ids if self.rows[i]["metadata"].get("source") == where["source"]]
        out = {"ids": ids}
        include = include or []
        if "documents" in include:
            out["documents"] = [self.rows[i]["document"] for i in ids]
        if "metadatas" in include:
            out["metadatas"] = [self.rows[i]["metadata"] for i in ids]
        if "embeddings" in include:
            out["embeddings"] = [self.rows[i]["embedding"] for i in ids]
        return out

    def delete(self, ids):
        for cid in ids:
            self.rows.pop(cid, None)

    def count(self):
        return len(self.rows)


def _stat(url, status="ok", chunks=5):
    return {"url": url, "status": status, "strategy": "selector",
            "chars": 1000, "chunks": chunks, "error": ""}


def _chunks_for(url, n=5, needle=""):
    return [{"id": f"{url}_chunk_{j}",
             "text": f"Document: T\nSource: {url}\n\ncontent {needle} {j}",
             "metadata": {"source": url, "title": "T", "extractor": "selector"}}
            for j in range(n)]


def _passing_inputs():
    stats, chunks = [], []
    for url, needle in KEY_PAGE_CHECKS.items():
        stats.append(_stat(url))
        chunks.extend(_chunks_for(url, needle=needle))
    fillers = [f"https://www.example-site.org/p{i}" for i in range(50)]
    for url in fillers:
        stats.append(_stat(url))
        chunks.extend(_chunks_for(url))
    return stats, chunks


def test_validate_passes_on_good_run():
    stats, chunks = _passing_inputs()
    assert chunks and len(chunks) >= MIN_TOTAL_CHUNKS
    assert validate(stats, chunks) == []


def test_validate_fails_on_low_success_rate():
    stats, chunks = _passing_inputs()
    stats.extend(_stat(f"https://www.example-site.org/f{i}", status="fetch_failed", chunks=0)
                 for i in range(20))
    errors = validate(stats, chunks)
    assert any("success rate" in e for e in errors)


def test_validate_fails_when_key_page_missing_text():
    stats, chunks = _passing_inputs()
    # Break the first configured key page, whatever site_config.KEY_PAGE_CHECKS
    # happens to hold — the gate is what's under test, not any one page.
    url, needle = next(iter(KEY_PAGE_CHECKS.items()))
    chunks = [c for c in chunks if c["metadata"]["source"] != url]
    chunks.extend(_chunks_for(url, needle="unrelated"))
    errors = validate(stats, chunks)
    assert any(needle in e.lower() for e in errors)


def test_validate_dead_urls_do_not_count_against_success_rate():
    stats, chunks = _passing_inputs()
    # 30 dead sitemap URLs would sink the rate if counted (63/93 = 68%)
    stats.extend(_stat(f"https://www.example-site.org/dead{i}", status="dead_url", chunks=0)
                 for i in range(30))
    assert validate(stats, chunks) == []


def test_validate_subset_mode_skips_global_checks():
    url = "https://www.example-site.org/p1"
    errors = validate([_stat(url)], _chunks_for(url), subset=True)
    assert errors == []


def test_swap_preserves_manual_sources_and_replaces_crawled():
    live, staging = FakeCollection(), FakeCollection()
    live.add(ids=["manual_1"], documents=["manual doc"],
             metadatas=[{"source": "site_profile.txt"}], embeddings=[[0.1]])
    live.add(ids=["https://www.example-site.org/a_chunk_0"], documents=["old"],
             metadatas=[{"source": "https://www.example-site.org/a"}], embeddings=[[0.2]])
    staging.add(ids=["https://www.example-site.org/a_chunk_0",
                     "https://www.example-site.org/a_chunk_1"],
                documents=["new0", "new1"],
                metadatas=[{"source": "https://www.example-site.org/a"}] * 2,
                embeddings=[[0.3], [0.4]])
    kept_foreign, removed = swap_into_live(live, staging, prune=False)
    assert "site_profile.txt" in kept_foreign
    assert removed == 1
    assert live.rows["https://www.example-site.org/a_chunk_0"]["document"] == "new0"
    assert "https://www.example-site.org/a_chunk_1" in live.rows
    assert "manual_1" in live.rows


def test_swap_prune_removes_everything_not_staged():
    live, staging = FakeCollection(), FakeCollection()
    live.add(ids=["manual_1"], documents=["manual doc"],
             metadatas=[{"source": "site_profile.txt"}], embeddings=[[0.1]])
    staging.add(ids=["https://www.example-site.org/a_chunk_0"], documents=["new0"],
                metadatas=[{"source": "https://www.example-site.org/a"}],
                embeddings=[[0.3]])
    kept_foreign, removed = swap_into_live(live, staging, prune=True)
    assert kept_foreign == set()
    assert "manual_1" not in live.rows
    assert "https://www.example-site.org/a_chunk_0" in live.rows
