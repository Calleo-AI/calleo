"""Unit tests for Database/discovery.py (pure functions, no network).

URLs are built from the configured SITE_ROOT so the tests keep passing when a
deployer edits school_config.py. Pattern-mechanism tests monkeypatch the
compiled exclusion list to test behavior independent of the shipped config.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

import discovery
from discovery import SITE_ROOT, parse_sitemap, normalize_url, is_excluded, filter_urls

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


def test_excluded_offsite_and_files():
    assert is_excluded("https://alumni.some-other-host.org")
    assert is_excluded("https://example.myschoolapp.com/app")
    assert is_excluded(f"{SITE_ROOT}/uploads/handbook.pdf")


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
    # school_config.py must exclude matching URLs (matched lowercase).
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
