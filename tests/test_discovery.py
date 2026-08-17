"""Unit tests for Database/discovery.py (pure functions, no network).

URLs are built from the configured SITE_ROOT so the tests keep passing when a
deployer edits site_config.py. Pattern-mechanism tests monkeypatch the
compiled exclusion list to test behavior independent of the shipped config.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

import discovery
from discovery import (
    SITE_ROOT,
    classify_url,
    classify_urls,
    document_type,
    filter_documents,
    filter_urls,
    is_document,
    is_excluded,
    normalize_url,
    parse_sitemap,
)

SITEMAP_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{SITE_ROOT}/about/at-a-glance</loc></url>
  <url><loc>{SITE_ROOT}/page/about/history</loc></url>
  <url><loc>{SITE_ROOT}/careers</loc></url>
</urlset>"""


def test_parse_sitemap_extracts_locs():
    assert parse_sitemap(SITEMAP_XML) == [
        f"{SITE_ROOT}/about/at-a-glance",
        f"{SITE_ROOT}/page/about/history",
        f"{SITE_ROOT}/careers",
    ]


def test_parse_sitemap_handles_missing_namespace():
    xml = f"<urlset><url><loc>{SITE_ROOT}/x</loc></url></urlset>"
    assert parse_sitemap(xml) == [f"{SITE_ROOT}/x"]


def test_normalize_strips_page_prefix():
    assert (
        normalize_url(f"{SITE_ROOT}/page/about/history")
        == f"{SITE_ROOT}/about/history"
    )


def test_normalize_strips_fragment_and_trailing_slash():
    assert (
        normalize_url(f"{SITE_ROOT}/academics/#section")
        == f"{SITE_ROOT}/academics"
    )
    assert normalize_url(f"{SITE_ROOT}/") == SITE_ROOT


def test_excluded_careers_and_fundraising():
    assert is_excluded(f"{SITE_ROOT}/careers/job-postings")
    assert is_excluded(f"{SITE_ROOT}/donate-now")


def test_excluded_query_strings_and_news():
    assert is_excluded(f"{SITE_ROOT}/News-Detail?pk=1300167")
    assert is_excluded(f"{SITE_ROOT}/news-detail?pk=2")


def test_excluded_offsite():
    assert is_excluded("https://alumni.some-other-host.org")
    assert is_excluded("https://example.myschoolapp.com/app")


def test_robots_disallowed_paths_do_not_overmatch():
    # /app and /api are robots-disallowed but must not exclude /how-to-apply
    assert is_excluded(f"{SITE_ROOT}/app")
    assert is_excluded(f"{SITE_ROOT}/api/foo")
    assert is_excluded(f"{SITE_ROOT}/calendar")
    assert not is_excluded(f"{SITE_ROOT}/how-to-apply/apply-now")


def test_included_core_content():
    assert not is_excluded(f"{SITE_ROOT}/family-handbook/dress-code")
    assert not is_excluded(f"{SITE_ROOT}/admissions/tuition-and-fees")


def test_excluded_site_map_nav_index():
    assert is_excluded(f"{SITE_ROOT}/site-map")


def test_custom_exclusion_patterns_are_honored(monkeypatch):
    # The mechanism: any regex a deployer adds to EXCLUDED_URL_PATTERNS in
    # site_config.py must exclude matching URLs (matched lowercase).
    patterns = [r"intersession-week-20\d\d", r"photo-gallery-"]
    monkeypatch.setattr(discovery, "_EXCLUDED_RES", [re.compile(p) for p in patterns])
    assert is_excluded(f"{SITE_ROOT}/intersession-week-2023")
    assert is_excluded(f"{SITE_ROOT}/school-photo-gallery-fall")
    assert not is_excluded(f"{SITE_ROOT}/admissions")


def test_filter_urls_normalizes_and_dedupes():
    urls = [
        f"{SITE_ROOT}/about/history",
        f"{SITE_ROOT}/page/about/history",
        f"{SITE_ROOT}/careers",
    ]
    assert filter_urls(urls) == [f"{SITE_ROOT}/about/history"]


# --- Document classification ------------------------------------------------
# Linked PDFs/DOCX used to be dropped by an r"\.pdf$" entry in
# EXCLUDED_URL_PATTERNS. They are now routed by extension instead, so the tests
# below pin down the routing AND both ways a deployer can still opt out.

def test_document_type_and_is_document():
    assert document_type(f"{SITE_ROOT}/uploads/handbook.pdf") == "pdf"
    assert document_type(f"{SITE_ROOT}/uploads/FORM.DOCX") == "docx"
    assert document_type(f"{SITE_ROOT}/about") == ""
    assert is_document(f"{SITE_ROOT}/uploads/handbook.pdf")
    assert not is_document(f"{SITE_ROOT}/about")


def test_pdf_is_classified_as_document():
    assert classify_url(f"{SITE_ROOT}/uploads/handbook.pdf") == "document"
    assert classify_url(f"{SITE_ROOT}/about/history") == "page"


def test_documents_off_makes_pdf_unindexable():
    assert classify_url(f"{SITE_ROOT}/uploads/handbook.pdf", allow_documents=False) == ""
    # ...while ordinary pages are unaffected.
    assert classify_url(f"{SITE_ROOT}/about", allow_documents=False) == "page"


def test_deployer_can_still_exclude_pdfs(monkeypatch):
    # The compatibility guarantee: putting r"\.pdf$" back into
    # EXCLUDED_URL_PATTERNS is a hard ban that beats CRAWL_DOCUMENTS.
    monkeypatch.setattr(discovery, "_EXCLUDED_RES", [re.compile(r"\.pdf$")])
    assert classify_url(f"{SITE_ROOT}/uploads/handbook.pdf") == ""
    assert classify_url(f"{SITE_ROOT}/about") == "page"


def test_classify_url_still_rejects_offsite_and_robots_paths():
    assert classify_url("https://alumni.some-other-host.org/report.pdf") == ""
    assert classify_url(f"{SITE_ROOT}/app/handbook.pdf") == ""
    assert classify_url(f"{SITE_ROOT}/careers/posting.pdf") == ""


def test_classify_urls_normalizes_dedupes_and_splits_kinds():
    pairs = classify_urls([
        f"{SITE_ROOT}/page/about/history",
        f"{SITE_ROOT}/about/history/",
        f"{SITE_ROOT}/uploads/handbook.pdf",
        f"{SITE_ROOT}/careers",
    ])
    assert pairs == [
        (f"{SITE_ROOT}/about/history", "page"),
        (f"{SITE_ROOT}/uploads/handbook.pdf", "document"),
    ]


def test_filter_documents_keeps_only_documents():
    assert filter_documents([
        f"{SITE_ROOT}/about",
        f"{SITE_ROOT}/uploads/handbook.pdf",
        f"{SITE_ROOT}/uploads/handbook.pdf",
    ]) == [f"{SITE_ROOT}/uploads/handbook.pdf"]
