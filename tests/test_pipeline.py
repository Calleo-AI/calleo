"""Tests for Database/pipeline.py build_chunks (no network; crawl_pages is not unit-tested)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

import pytest

import documents
from pipeline import ERR_TOO_LARGE, ERR_UNSUPPORTED, build_chunks

FIXTURES = Path(__file__).parent / "fixtures"

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


# --- Backward compatibility --------------------------------------------------
# Every test above builds page dicts with the original five keys and no "kind".
# These lock in that the new fields are additive and default correctly.

def test_pages_without_a_kind_key_are_treated_as_html():
    chunks, stats = build_chunks([_page("https://www.example-site.org/x", CONTENT_HTML)])
    assert stats[0]["kind"] == "page"
    assert chunks[0]["metadata"]["kind"] == "page"


# --- Images ------------------------------------------------------------------

def _gallery_page(url="https://www.example-site.org/campus-life"):
    html = (FIXTURES / "gallery-with-images.html").read_text(encoding="utf-8")
    return _page(url, html)


def test_image_chunks_are_appended_and_keyed_to_their_page():
    url = "https://www.example-site.org/campus-life"
    chunks, stats = build_chunks([_gallery_page(url)], index_images=True)
    image_chunks = [c for c in chunks if c["metadata"]["kind"] == "image"]
    assert image_chunks
    assert stats[0]["images"] == len(image_chunks)
    assert all(c["metadata"]["source"] == url for c in image_chunks)
    # Content first, images last — a truncated build keeps prose over pictures.
    assert chunks[0]["metadata"]["kind"] == "page"
    assert chunks[-1]["metadata"]["kind"] == "image"


def test_images_can_be_switched_off():
    chunks, stats = build_chunks([_gallery_page()], index_images=False)
    assert not [c for c in chunks if c["metadata"]["kind"] == "image"]
    assert stats[0]["images"] == 0


def test_images_are_harvested_from_pages_too_thin_to_index_as_prose():
    # A gallery shell has almost no prose but is exactly where the pictures are.
    html = ('<html><body><div class="page-row"><div class="page-col span24">'
            '<h1 class="page-title">Gallery</h1>'
            '<img src="/uploads/hall.jpg" alt="The dining hall during a shared lunch">'
            '</div></div></body></html>')
    chunks, stats = build_chunks([_page("https://www.example-site.org/gallery", html)])
    assert stats[0]["status"] == "thin"
    assert [c["metadata"]["kind"] for c in chunks] == ["image"]


def test_images_on_a_soft_404_page_are_not_indexed():
    body = ("<html><body><div class='page-row'><div class='page-col span24'><p>"
            + "We're sorry. But the page or file you requested does not exist. " * 10
            + '</p><img src="/uploads/x.jpg" alt="A photograph on an error page">'
            "</div></div></body></html>")
    chunks, stats = build_chunks([_page("https://www.example-site.org/gone", body)])
    assert chunks == []
    assert stats[0]["status"] == "dead_url"


# --- Documents ---------------------------------------------------------------

DOC_URL = "https://www.example-site.org/uploads/family-handbook.pdf"


def _doc(url=DOC_URL, content=b"%PDF-", success=True, error="",
         content_type="application/pdf"):
    return {"url": url, "html": "", "fit_markdown": "", "success": success,
            "error": error, "kind": "document", "content": content,
            "content_type": content_type}


@pytest.fixture
def fake_pdf_text(monkeypatch):
    def _set(text):
        monkeypatch.setattr(documents, "extract_pdf",
                            lambda data, max_pages=None: (text, 1))
    return _set


def test_document_chunks_are_sourced_to_the_document_itself(fake_pdf_text):
    fake_pdf_text("The dress code applies on campus and at school events. " * 20)
    chunks, stats = build_chunks([_doc()])
    assert stats[0]["kind"] == "document"
    assert stats[0]["status"] == "ok"
    assert stats[0]["strategy"] == "pdf"
    assert chunks[0]["id"] == f"{DOC_URL}_chunk_0"
    assert chunks[0]["metadata"]["source"] == DOC_URL
    assert chunks[0]["metadata"]["kind"] == "document"
    assert chunks[0]["text"].startswith("Document: Family Handbook")


def test_scanned_document_with_no_text_layer_is_thin_not_indexed(fake_pdf_text):
    fake_pdf_text("")
    chunks, stats = build_chunks([_doc()])
    assert chunks == []
    assert stats[0]["status"] == "thin"


def test_oversized_and_unsupported_documents_get_their_own_statuses():
    _, stats = build_chunks([
        _doc(success=False, error=f"{ERR_TOO_LARGE}: 90000000 bytes"),
        _doc(success=False, error=f"{ERR_UNSUPPORTED}: video/mp4"),
    ])
    assert [s["status"] for s in stats] == ["too_large", "unsupported_type"]


def test_a_missing_optional_dependency_costs_one_document_not_the_rebuild(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("pypdf is required to index documents")
    monkeypatch.setattr(documents, "extract_document", boom)

    chunks, stats = build_chunks([
        _doc(),
        _page("https://www.example-site.org/clubs/robotics", CONTENT_HTML),
    ])
    assert stats[0]["status"] == "extract_failed"
    assert "pypdf is required" in stats[0]["error"]
    assert chunks          # the HTML page in the same batch still produced chunks
