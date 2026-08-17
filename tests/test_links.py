"""Tests for Database/links.py — link harvesting from raw HTML (pure, no network)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

from discovery import SITE_ROOT
from links import extract_links, is_same_site, to_site_root

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = f"{SITE_ROOT}/campus-life"


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8", errors="ignore")


def test_extracts_internal_links_and_absolutizes_them():
    links = extract_links(_fixture("gallery-with-images.html"), PAGE_URL)
    assert f"{SITE_ROOT}/clubs/robotics" in links
    assert f"{SITE_ROOT}/uploads/family-handbook.pdf" in links
    assert all(link.startswith(SITE_ROOT) for link in links)


def test_drops_offsite_schemes_fragments_and_nofollow():
    links = extract_links(_fixture("gallery-with-images.html"), PAGE_URL)
    assert not any("partner.example.net" in link for link in links)
    assert not any(link.startswith(("mailto:", "tel:")) for link in links)
    assert PAGE_URL not in links                    # "#top" is not a new URL
    assert f"{SITE_ROOT}/private/notes" not in links  # rel="nofollow"


def test_normalizes_and_dedupes_preserving_order():
    # /page/about/history and /about/history/ are the same page written two ways.
    links = extract_links(_fixture("gallery-with-images.html"), PAGE_URL)
    assert links.count(f"{SITE_ROOT}/about/history") == 1


def test_excluded_urls_are_left_for_discovery_to_reject():
    # links.py is deliberately policy-free: /careers and /site-map come out here
    # and are dropped by discovery.classify_url, which is the single gate.
    links = extract_links(_fixture("gallery-with-images.html"), PAGE_URL)
    assert f"{SITE_ROOT}/careers" in links
    assert f"{SITE_ROOT}/site-map" in links


def test_relative_hrefs_resolve_against_the_page_not_the_root():
    html = '<a href="teachers">Teachers</a>'
    assert extract_links(html, f"{SITE_ROOT}/about/staff") == [f"{SITE_ROOT}/about/teachers"]


def test_base_href_is_honored():
    html = f'<head><base href="{SITE_ROOT}/handbook/"></head><body><a href="rules">R</a>'
    assert extract_links(html, f"{SITE_ROOT}/anything") == [f"{SITE_ROOT}/handbook/rules"]


def test_same_site_treats_www_and_bare_host_as_one():
    bare = SITE_ROOT.replace("://www.", "://")
    assert is_same_site(f"{bare}/about")
    assert is_same_site(f"{SITE_ROOT}/about")
    assert not is_same_site("https://some-other-host.org/about")


def test_to_site_root_canonicalizes_scheme_and_host():
    bare = SITE_ROOT.replace("://www.", "://").replace("https://", "http://")
    assert to_site_root(f"{bare}/about/history") == f"{SITE_ROOT}/about/history"


def test_scheme_variant_links_dedupe_against_canonical_ones():
    bare = SITE_ROOT.replace("://www.", "://").replace("https://", "http://")
    html = f'<a href="{SITE_ROOT}/about">A</a><a href="{bare}/about">B</a>'
    assert extract_links(html, PAGE_URL) == [f"{SITE_ROOT}/about"]


def test_empty_html_is_safe():
    assert extract_links("", PAGE_URL) == []
    assert extract_links(None, PAGE_URL) == []
