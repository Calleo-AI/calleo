"""Tests for the rewritten update_db.py (crawl-first refresh; no network)."""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("CHROMA_DB_PATH", os.path.join(os.path.dirname(__file__), "_tmp_chroma"))

import update_db
from test_create_db import FakeCollection

URL = "https://www.example-site.org/clubs/robotics"
CONTENT_HTML = (
    "<html><head><title>Robotics</title></head><body>"
    "<div class='page-row'><div class='page-col span24'>"
    "<h1 class='page-title'>Robotics</h1>"
    + "<p>Team 610 competes in FIRST Robotics and mentors younger students. </p>" * 20
    + "</div></div></body></html>"
)


def _seed(collection, url, n=3):
    collection.add(
        ids=[f"{url}_chunk_{j}" for j in range(n)],
        documents=[f"old {j}" for j in range(n)],
        metadatas=[{"source": url} for _ in range(n)],
        embeddings=[[0.1]] * n,
    )


def _fake_crawl(pages):
    async def crawl(urls):
        return [p for p in pages if p["url"] in urls]
    return crawl


def test_parse_args_defaults():
    args = update_db.parse_args([])
    assert args.urls == []
    assert args.collection == "full_database"
    assert not args.dry_run


def test_refresh_replaces_chunks_on_success():
    col = FakeCollection()
    _seed(col, URL, n=3)
    page = {"url": URL, "html": CONTENT_HTML, "fit_markdown": "", "success": True, "error": ""}
    with patch.object(update_db, "crawl_pages", _fake_crawl([page])):
        removed, added = asyncio.run(update_db.refresh_urls(col, [URL]))
    assert removed == 3
    assert added >= 1
    assert all(r["metadata"]["source"] == URL for r in col.rows.values())
    assert not any(str(d["document"]).startswith("old") for d in col.rows.values())


def test_refresh_keeps_old_chunks_when_fetch_fails():
    col = FakeCollection()
    _seed(col, URL, n=3)
    page = {"url": URL, "html": "", "fit_markdown": "", "success": False, "error": "timeout"}
    with patch.object(update_db, "crawl_pages", _fake_crawl([page])):
        removed, added = asyncio.run(update_db.refresh_urls(col, [URL]))
    assert removed == 0 and added == 0
    assert col.count() == 3  # old data preserved — no delete-before-crawl


def test_refresh_dry_run_changes_nothing():
    col = FakeCollection()
    _seed(col, URL, n=3)
    page = {"url": URL, "html": CONTENT_HTML, "fit_markdown": "", "success": True, "error": ""}
    with patch.object(update_db, "crawl_pages", _fake_crawl([page])):
        removed, added = asyncio.run(update_db.refresh_urls(col, [URL], dry_run=True))
    assert col.count() == 3
    assert added == 0


def test_get_stored_urls_skips_manual_sources():
    col = FakeCollection()
    _seed(col, URL, n=2)
    col.add(ids=["manual_1"], documents=["doc"],
            metadatas=[{"source": "site_profile.txt"}], embeddings=[[0.1]])
    urls = update_db.get_stored_urls(col)
    assert urls == [URL]
