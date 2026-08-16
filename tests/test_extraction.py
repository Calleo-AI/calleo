"""Tests for Database/extraction.py against synthetic Blackbaud-shaped fixtures."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

from extraction import (
    extract_content,
    extract_title,
    scrub_junk_lines,
    selector_extract,
    MIN_CHARS,
)

FIXTURES = Path(__file__).parent / "fixtures"
TUITION_URL = "https://www.example-site.org/admissions/tuition-and-fees"
GLANCE_URL = "https://www.example-site.org/about/at-a-glance"


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8", errors="ignore")


def test_selector_extracts_tuition_content():
    text, strategy = extract_content(_fixture("tuition-and-fees.html"), TUITION_URL)
    assert strategy == "selector"
    assert "45,000" in text
    assert len(text) >= MIN_CHARS


def test_selector_output_has_no_chrome_or_junk():
    text, _ = extract_content(_fixture("at-a-glance.html"), GLANCE_URL)
    assert "List of" not in text          # .element-invisible a11y stubs removed
    assert "Login" not in text            # header chrome removed
    assert "myschoolapp" not in text
    assert "Quick Links" not in text


def test_handbook_template_extracts():
    text, strategy = extract_content(
        _fixture("handbook-mission.html"),
        "https://www.example-site.org/family-handbook/mission-and-values",
    )
    assert len(text) >= MIN_CHARS
    assert strategy == "selector"


def test_fallback_to_trafilatura_when_no_page_rows():
    para = "<p>Example Site provides rich opportunities for the community to learn and grow. </p>"
    html = f"<html><head><title>T</title></head><body><article><h1>Programme</h1>{para * 20}</article></body></html>"
    text, strategy = extract_content(html, "https://www.example-site.org/x")
    assert strategy == "trafilatura"
    assert "opportunities for the community" in text


def test_thin_page_returns_best_effort_below_min():
    text, _ = extract_content("<html><body><p>tiny</p></body></html>",
                              "https://www.example-site.org/x", fit_markdown="tiny")
    assert len(text) < MIN_CHARS  # caller is responsible for skipping


def test_scrub_junk_lines():
    md = "Real content here.\n\nList of 6 items.\n\nList of 4 frequently asked questions.\n\nMore content."
    out = scrub_junk_lines(md)
    assert "List of" not in out
    assert "Real content" in out and "More content" in out


def test_trafilatura_fallback_does_not_extract_nav_menus():
    # A content-less page (e.g. photo gallery) whose only sizable text is the
    # site-wide mega-menu must classify as thin, not return nav junk.
    menu_items = "".join(f"<li><a href='/x{i}'>Menu Item Number {i} arrow</a></li>" for i in range(60))
    html = (
        "<html><body>"
        f"<div class='content megamenu'><ul>{menu_items}</ul></div>"
        "<div class='page-row'><p>1 / 37</p></div>"
        "</body></html>"
    )
    text, _ = extract_content(html, "https://www.example-site.org/gallery")
    assert len(text) < MIN_CHARS
    assert "Menu Item Number" not in text


def test_scrub_removes_empty_bullet_lines_but_keeps_tables():
    md = "Intro.\n\n  *     * \n\nFee | $225\n---|---\nDone."
    out = scrub_junk_lines(md)
    assert "*" not in out
    assert "---|---" in out and "Fee | $225" in out


def test_extract_title_prefers_page_h1():
    title = extract_title(_fixture("tuition-and-fees.html"), TUITION_URL)
    assert "tuition" in title.lower()


def test_extract_title_slug_fallback():
    assert extract_title("<html></html>", TUITION_URL) == "Tuition And Fees"
