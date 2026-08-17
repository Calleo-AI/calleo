"""Tests for Database/images.py — images indexed as text records (pure, no network)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

import images
from discovery import SITE_ROOT
from images import build_chunks, harvest, record_id, record_text

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = f"{SITE_ROOT}/campus-life"
PAGE_TITLE = "Campus Life"


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8", errors="ignore")


def _harvest(**kwargs):
    return harvest(_fixture("gallery-with-images.html"), PAGE_URL, PAGE_TITLE, **kwargs)


def _urls(records):
    return [r["image_url"] for r in records]


def test_harvests_described_content_images():
    urls = _urls(_harvest())
    assert f"{SITE_ROOT}/uploads/campus-tour_2019_1920x1080.jpg" in urls
    assert f"{SITE_ROOT}/uploads/community-garden.jpg" in urls   # data-src lazy load
    assert f"{SITE_ROOT}/uploads/hall-800.jpg" in urls           # first srcset candidate


def test_site_chrome_images_never_reach_the_index():
    # The header logo and footer mark are removed with the rest of the chrome
    # before any deny pattern runs — same soup the text extractor works from.
    urls = _urls(_harvest())
    assert not any("site-logo" in u or "footer-logo" in u for u in urls)


def test_decorative_and_undescribed_images_are_skipped():
    urls = _urls(_harvest())
    assert not any("spacer" in u for u in urls)        # EXCLUDED_IMAGE_PATTERNS
    assert not any(u.startswith("data:") for u in urls)  # inline tracking beacon
    assert not any("tiny-badge" in u for u in urls)    # declared 24x24
    assert not any("unlabelled" in u for u in urls)    # alt="photo" is a stopword


def test_relative_src_is_resolved_against_the_page():
    record = next(r for r in _harvest() if "campus-tour" in r["image_url"])
    assert record["image_url"] == f"{SITE_ROOT}/uploads/campus-tour_2019_1920x1080.jpg"


def test_caption_and_nearest_preceding_heading_are_captured():
    record = next(r for r in _harvest() if "campus-tour" in r["image_url"])
    assert record["caption"] == "The Team 610 build season, November 2025."
    assert record["heading"] == "Robotics"
    # The garden image sits under a later heading, so it must not inherit "Robotics".
    garden = next(r for r in _harvest() if "community-garden" in r["image_url"])
    assert garden["heading"] == "Garden"


def test_filename_is_cleaned_of_dates_dimensions_and_noise():
    record = next(r for r in _harvest() if "campus-tour" in r["image_url"])
    assert record["filename"] == "Campus Tour"


def test_min_alt_chars_gate_counts_only_human_written_text(monkeypatch):
    html = ('<h2>A heading long enough to clear the bar on its own</h2>'
            '<img src="/uploads/photo.jpg" alt="short">')
    assert harvest(html, PAGE_URL, PAGE_TITLE) == []
    monkeypatch.setattr(images, "IMAGE_MIN_ALT_CHARS", 3)
    assert len(harvest(html, PAGE_URL, PAGE_TITLE)) == 1


def test_max_images_per_page_cap():
    html = "".join(
        f'<img src="/uploads/photo{i}.jpg" alt="A clearly described classroom photo {i}">'
        for i in range(10)
    )
    assert len(harvest(html, PAGE_URL, PAGE_TITLE, max_images=3)) == 3


def test_record_id_is_content_addressed_and_stable():
    records = _harvest()
    first = record_id(records[0])
    assert first == record_id(records[0])
    assert first.startswith(f"{PAGE_URL}_image_")
    # Reordering the gallery must not churn ids.
    assert {record_id(r) for r in records} == {record_id(r) for r in reversed(records)}


def test_chunk_body_carries_the_image_url_and_survives_header_split():
    chunks, counts = build_chunks(_harvest())
    chunk = next(c for c in chunks if "campus-tour" in c["metadata"]["image_url"])
    # validate() checks the body only, i.e. everything after the contextual header.
    body = chunk["text"].split("\n\n", 1)[-1]
    assert chunk["metadata"]["image_url"] in body
    assert "Students assembling a competition chassis" in body
    assert chunk["text"].startswith(f"Document: {PAGE_TITLE}")
    assert counts[PAGE_URL] == len(chunks)


def test_chunk_metadata_keys_the_image_to_its_host_page():
    chunks, _ = build_chunks(_harvest())
    meta = chunks[0]["metadata"]
    assert meta["source"] == PAGE_URL          # NOT the image URL — see images.py docstring
    assert meta["kind"] == "image"
    assert meta["extractor"] == "image"
    assert meta["title"] == PAGE_TITLE
    assert None not in meta.values()           # Chroma rejects None metadata values


def test_same_image_described_identically_on_two_pages_is_embedded_once():
    html = '<img src="/uploads/shared.jpg" alt="The same shared photograph of the hall">'
    records = (harvest(html, f"{SITE_ROOT}/a", "Shared")
               + harvest(html, f"{SITE_ROOT}/b", "Shared"))
    chunks, _ = build_chunks(records)
    assert len(chunks) == 1


def test_same_image_described_differently_is_kept_twice():
    a = '<img src="/uploads/shared.jpg" alt="The hall set up for a community lunch">'
    b = '<img src="/uploads/shared.jpg" alt="The hall set up for graduation ceremonies">'
    records = (harvest(a, f"{SITE_ROOT}/a", "Lunch")
               + harvest(b, f"{SITE_ROOT}/b", "Graduation"))
    chunks, _ = build_chunks(records)
    assert len(chunks) == 2


def test_record_text_names_the_page_and_lists_context():
    record = next(r for r in _harvest() if "campus-tour" in r["image_url"])
    text = record_text(record)
    assert f'Image on the page "{PAGE_TITLE}"' in text
    assert "Caption: The Team 610 build season" in text
    assert "Nearby heading: Robotics" in text
    assert f"Image URL: {record['image_url']}" in text
    assert f"Appears on: {PAGE_URL}" in text
