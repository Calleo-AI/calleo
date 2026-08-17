"""Tests for Database/frontier.py — BFS link crawl driven by a fake fetcher (no network)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Database"))

from discovery import SITE_ROOT
from frontier import Frontier, crawl_site


def _crawl(seeds, site, **kwargs):
    """asyncio.run wrapper — the house pattern (see tests/test_update_db.py),
    so the suite needs no pytest-asyncio dependency."""
    return asyncio.run(crawl_site(seeds, site, **kwargs))


def _u(path):
    return f"{SITE_ROOT}{path}"


def _html(*paths):
    return "".join(f'<a href="{p}">link</a>' for p in paths)


class FakeSite:
    """A dict-backed stand-in for pipeline.crawl_pages.

    Records every fetch so the tests can assert on request count and ordering,
    which is what "link expansion costs no extra requests" actually means.
    """

    def __init__(self, pages):
        self.pages = pages           # {url: html}
        self.fetched = []

    async def __call__(self, urls, kinds=None):
        kinds = kinds or {}
        out = []
        for url in urls:
            self.fetched.append(url)
            html = self.pages.get(url)
            out.append({
                "url": url,
                "html": html or "",
                "fit_markdown": "",
                "success": html is not None,
                "error": "" if html is not None else "HTTP 404",
                "kind": kinds.get(url, "page"),
                "content": b"",
                "content_type": "",
            })
        return out


LINEAR_SITE = {
    _u("/a"): _html("/b"),
    _u("/b"): _html("/c"),
    _u("/c"): _html("/d"),
    _u("/d"): "",
}


def test_depth_zero_reproduces_sitemap_only_behavior():
    site = FakeSite(LINEAR_SITE)
    pages, stats = _crawl([_u("/a")], site, max_depth=0)
    assert [p["url"] for p in pages] == [_u("/a")]
    assert stats["link_pages"] == 0


def test_links_are_followed_up_to_max_depth():
    site = FakeSite(LINEAR_SITE)
    pages, stats = _crawl([_u("/a")], site, max_depth=2)
    assert [p["url"] for p in pages] == [_u("/a"), _u("/b"), _u("/c")]
    assert stats["depth_reached"] == 2
    assert stats["seeds"] == 1
    assert stats["link_pages"] == 2


def test_pages_carry_the_depth_they_were_found_at():
    site = FakeSite(LINEAR_SITE)
    pages, _ = _crawl([_u("/a")], site, max_depth=2)
    assert {p["url"]: p["depth"] for p in pages} == {
        _u("/a"): 0, _u("/b"): 1, _u("/c"): 2,
    }


def test_cycles_terminate_and_nothing_is_fetched_twice():
    site = FakeSite({_u("/a"): _html("/b"), _u("/b"): _html("/a", "/b")})
    pages, _ = _crawl([_u("/a")], site, max_depth=5)
    assert sorted(site.fetched) == [_u("/a"), _u("/b")]
    assert len(pages) == 2


def test_links_back_to_a_seed_are_not_refetched():
    site = FakeSite({_u("/a"): _html("/b"), _u("/b"): _html("/a")})
    _crawl([_u("/a"), _u("/b")], site, max_depth=3)
    assert sorted(site.fetched) == [_u("/a"), _u("/b")]


def test_excluded_and_offsite_links_are_never_queued():
    site = FakeSite({
        _u("/a"): _html("/careers", "/site-map", "/app/portal", "/about")
                  + '<a href="https://elsewhere.example.net/x">off</a>',
        _u("/about"): "",
    })
    pages, stats = _crawl([_u("/a")], site, max_depth=2)
    assert sorted(p["url"] for p in pages) == [_u("/a"), _u("/about")]
    # Off-site links never reach the frontier at all — links.py drops them at
    # harvest time — so only the three on-site exclusions are counted here.
    assert stats["skipped_policy"] == 3   # careers, site-map, /app/portal


def test_link_page_budget_caps_discovery_but_never_seeds():
    site = FakeSite({
        _u("/a"): _html("/x1", "/x2", "/x3"),
        _u("/b"): "",
        **{_u(f"/x{i}"): "" for i in range(1, 4)},
    })
    pages, stats = _crawl([_u("/a"), _u("/b")], site, max_depth=1, max_pages=1)
    assert stats["seeds"] == 2          # both seeds survive the budget
    assert stats["link_pages"] == 1
    assert stats["skipped_budget"] == 2
    assert len(pages) == 3


def test_documents_are_fetched_but_never_expanded():
    site = FakeSite({
        _u("/a"): _html("/uploads/handbook.pdf"),
        _u("/uploads/handbook.pdf"): _html("/should-not-be-followed"),
        _u("/should-not-be-followed"): "",
    })
    pages, stats = _crawl([_u("/a")], site, max_depth=3)
    assert sorted(p["url"] for p in pages) == [_u("/a"), _u("/uploads/handbook.pdf")]
    assert stats["documents"] == 1
    doc = next(p for p in pages if p["url"].endswith(".pdf"))
    assert doc["kind"] == "document"


def test_documents_can_be_turned_off():
    site = FakeSite({_u("/a"): _html("/uploads/handbook.pdf")})
    pages, stats = _crawl([_u("/a")], site, max_depth=2, allow_documents=False)
    assert [p["url"] for p in pages] == [_u("/a")]
    assert stats["documents"] == 0


def test_a_dead_page_does_not_stop_the_crawl():
    site = FakeSite({_u("/a"): _html("/missing", "/b"), _u("/b"): ""})
    pages, _ = _crawl([_u("/a")], site, max_depth=1)
    assert sorted(p["url"] for p in pages) == [_u("/a"), _u("/b"), _u("/missing")]
    assert next(p for p in pages if p["url"] == _u("/missing"))["success"] is False


def test_breadth_first_order_all_of_one_depth_before_the_next():
    site = FakeSite({
        _u("/a"): _html("/b1", "/b2"),
        _u("/b1"): _html("/c1"),
        _u("/b2"): _html("/c2"),
        _u("/c1"): "", _u("/c2"): "",
    })
    _, _ = _crawl([_u("/a")], site, max_depth=2, batch_size=16)
    depth1 = {_u("/b1"), _u("/b2")}
    depth2 = {_u("/c1"), _u("/c2")}
    last_depth1 = max(site.fetched.index(u) for u in depth1)
    first_depth2 = min(site.fetched.index(u) for u in depth2)
    assert last_depth1 < first_depth2


def test_frontier_normalizes_seeds_and_drops_duplicates():
    frontier = Frontier([_u("/page/about/history"), _u("/about/history/"), _u("/careers")])
    assert [url for url, _, _ in frontier.queue] == [_u("/about/history")]


def test_frontier_refuses_urls_past_max_depth():
    frontier = Frontier([_u("/a")], max_depth=1)
    assert frontier.add(_u("/b"), "page", 1) is True
    assert frontier.add(_u("/c"), "page", 2) is False
    assert frontier.stats()["skipped_depth"] == 1
