"""Tests for Database/pipeline.py build_chunks (no network; crawl_pages is not unit-tested)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

from pipeline import build_chunks

CONTENT_HTML = (
    "<html><head><title>Robotics | Example School</title></head><body>"
    "<div class='page-row'><div class='page-col span24'>"
    "<h1 class='page-title'>Robotics</h1>"
    + "<p>Team 610 competes in FIRST Robotics and mentors younger students. </p>" * 20
    + "</div></div></body></html>"
)


def _page(url, html="", success=True, error="", fit_markdown=""):
    return {"url": url, "html": html, "success": success,
            "error": error, "fit_markdown": fit_markdown}


def test_build_chunks_success_page():
    url = "https://www.example-site.org/clubs/robotics"
    chunks, stats = build_chunks([_page(url, CONTENT_HTML)])
    assert stats[0]["status"] == "ok"
    assert stats[0]["strategy"] == "selector"
    assert chunks
    assert chunks[0]["id"] == f"{url}_chunk_0"
    assert chunks[0]["metadata"]["source"] == url
    assert chunks[0]["metadata"]["extractor"] == "selector"
    assert chunks[0]["text"].startswith("Document: Robotics")


def test_build_chunks_failed_fetch():
    chunks, stats = build_chunks([_page("https://x", success=False, error="timeout")])
    assert chunks == []
    assert stats[0]["status"] == "fetch_failed"


def test_build_chunks_thin_page_skipped():
    chunks, stats = build_chunks(
        [_page("https://www.example-site.org/x", "<html><body><p>tiny</p></body></html>")]
    )
    assert chunks == []
    assert stats[0]["status"] == "thin"


def test_build_chunks_http_404_is_dead_url():
    chunks, stats = build_chunks(
        [_page("https://www.example-site.org/faqs", success=False, error="HTTP 404")]
    )
    assert chunks == []
    assert stats[0]["status"] == "dead_url"


def test_build_chunks_soft_404_boilerplate_is_dead_url():
    body = ("<html><body><div class='page-row'><div class='page-col span24'><p>"
            + "We're sorry. But the page or file you requested does not exist. " * 10
            + "</p></div></div></body></html>")
    chunks, stats = build_chunks([_page("https://www.example-site.org/old-page", body)])
    assert chunks == []
    assert stats[0]["status"] == "dead_url"
    assert stats[0]["error"] == "soft 404"
