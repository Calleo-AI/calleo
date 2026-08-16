"""Unit tests for Database/chunking.py (pure functions)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

from chunking import chunk_page, build_header, WHOLE_PAGE_MAX

URL = "https://www.example-site.org/admissions/dates-and-events"
TITLE = "Admission Dates & Events"


def test_build_header_with_breadcrumb():
    h = build_header(TITLE, URL, "Admissions > Important Dates")
    assert "Document: Admission Dates & Events" in h
    assert f"Source: {URL}" in h
    assert "Section: Admissions > Important Dates" in h
    assert h.endswith("\n\n")


def test_small_page_stays_whole_with_header():
    md = "# About\n\nExample Site is a non-profit based in Springfield."
    chunks = chunk_page(md, TITLE, URL)
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("Document: ")
    assert "non-profit based in Springfield" in chunks[0]["text"]
    assert chunks[0]["metadata"]["source"] == URL
    assert chunks[0]["metadata"]["title"] == TITLE


def test_large_page_splits_on_headings_and_keeps_context():
    dates = "January 19-30, 2026: Grades 1 & 2 Interviews. " * 10
    process = "Applicants complete the online form and submit transcripts. " * 60
    md = f"# Admissions\n\n## Important Dates\n\n{dates}\n\n## Process\n\n{process}"
    assert len(md) > WHOLE_PAGE_MAX
    chunks = chunk_page(md, TITLE, URL)
    assert len(chunks) >= 2
    dates_chunks = [c for c in chunks if "Grades 1 & 2 Interviews" in c["text"]]
    assert dates_chunks, "date content lost"
    # The fix for the original recall bug: dates travel with page + section context
    for c in dates_chunks:
        assert "Document: Admission Dates & Events" in c["text"]
        assert "Important Dates" in c["text"]
    assert all("section" in c["metadata"] for c in chunks if "Process" in c["text"])


def test_heading_stub_merges_forward_with_following_content():
    filler = "General information about the school programme. " * 50
    md = f"# Page\n\n{filler}\n\n## Important Dates\n\n- Jan 5: Open House\n- Feb 2: Assessments"
    chunks = chunk_page(md, TITLE, URL)
    stub_chunks = [c for c in chunks if "Open House" in c["text"]]
    assert stub_chunks
    assert "Important Dates" in stub_chunks[0]["text"]


def test_oversize_section_subsplit_repeats_header():
    body = "The programme builds character through mentorship and service. " * 80
    md = f"# Page\n\n## Programme\n\n{body}"
    chunks = chunk_page(md, TITLE, URL)
    assert len(chunks) >= 2
    for c in chunks:
        assert c["text"].startswith("Document: ")
        assert c["metadata"]["section"]


def test_junk_fragment_dropped():
    filler = "Substantive content about academics at the school. " * 60
    md = f"# Page\n\n## Academics\n\n{filler}\n\n## !!!\n\n- *\n- *"
    chunks = chunk_page(md, TITLE, URL)
    assert all(sum(ch.isalnum() for ch in c["text"].split("\n\n", 1)[-1]) >= 50 for c in chunks)


def test_empty_markdown_returns_no_chunks():
    assert chunk_page("", TITLE, URL) == []
    assert chunk_page("   \n  ", TITLE, URL) == []
