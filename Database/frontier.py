"""
frontier.py — Breadth-first link crawl on top of the sitemap seeds.

The sitemap tells us what a site *declares*; the links on its pages tell us
what it actually has. This module walks outward from the seed URLs, harvesting
<a href> targets out of HTML we have already fetched — link expansion costs no
extra HTTP requests, only the fetches of the newly discovered URLs themselves.

Policy (what counts as a page, a document, or nothing) lives in discovery.py;
fetching lives in pipeline.py. This module only owns the queue, the depth and
budget accounting, and the loop that connects the two. `fetch` is injected, so
the whole traversal is unit-testable with a dict-backed fake and no network.
"""
import sys
from pathlib import Path

import discovery
from links import extract_links

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_config import CRAWL_MAX_DEPTH, CRAWL_MAX_PAGES

BATCH_SIZE = 16   # 4x pipeline.CONCURRENCY: keeps workers fed without losing BFS order


class Frontier:
    """BFS queue with depth and page-budget accounting.

    Seeds enter at depth 0 and are never refused — the sitemap is authoritative
    and `--max-pages` already caps it. The budget applies to link-discovered
    pages only, so CRAWL_MAX_PAGES reads as "how much extra crawling am I
    willing to pay for". Documents are terminal: fetched, never expanded.
    """

    def __init__(self, seeds, max_depth=None, max_pages=None, allow_documents=None):
        self.max_depth = CRAWL_MAX_DEPTH if max_depth is None else max_depth
        self.max_pages = CRAWL_MAX_PAGES if max_pages is None else max_pages
        self.allow_documents = allow_documents
        self.seen = set()
        self.queue = []
        self.counts = {"seed": 0, "page": 0, "document": 0}
        self.skipped = {"depth": 0, "budget": 0, "policy": 0}
        for url, kind in discovery.classify_urls(seeds, allow_documents=allow_documents):
            self.seen.add(url)
            self.queue.append((url, kind, 0))
            self.counts["seed" if kind == "page" else "document"] += 1

    def _over_budget(self):
        return bool(self.max_pages) and self.counts["page"] >= self.max_pages

    def add(self, url, kind, depth):
        """Queue one already-classified URL. Returns True if it was accepted."""
        if url in self.seen:
            return False
        if depth > self.max_depth:
            self.skipped["depth"] += 1
            return False
        if kind == "page" and self._over_budget():
            self.skipped["budget"] += 1
            return False
        self.seen.add(url)
        self.queue.append((url, kind, depth))
        self.counts[kind] += 1
        return True

    def add_links(self, urls, parent_depth):
        """Classify and queue links harvested from a page at parent_depth."""
        depth = parent_depth + 1
        added = 0
        classified = discovery.classify_urls(urls, allow_documents=self.allow_documents)
        self.skipped["policy"] += len(set(urls)) - len(classified)
        for url, kind in classified:
            if self.add(url, kind, depth):
                added += 1
        return added

    def next_batch(self, size=BATCH_SIZE):
        batch, self.queue = self.queue[:size], self.queue[size:]
        return batch

    def pending(self):
        return len(self.queue)

    def stats(self):
        return {
            "seeds": self.counts["seed"],
            "link_pages": self.counts["page"],
            "documents": self.counts["document"],
            "seen": len(self.seen),
            "max_depth": self.max_depth,
            **{f"skipped_{k}": v for k, v in self.skipped.items()},
        }


async def crawl_site(seeds, fetch, max_depth=None, max_pages=None,
                     allow_documents=None, batch_size=BATCH_SIZE):
    """BFS from `seeds`, following same-site links. Returns (pages, stats).

    `fetch(urls, kinds=...)` is injected — create_db.py passes
    pipeline.crawl_pages. Each returned page dict is stamped with the `kind`
    and `depth` it was queued at, which is what build_chunks dispatches on.
    """
    frontier = Frontier(seeds, max_depth=max_depth, max_pages=max_pages,
                        allow_documents=allow_documents)
    pages, depth_reached = [], 0

    while frontier.pending():
        batch = frontier.next_batch(batch_size)
        kinds = {url: kind for url, kind, _ in batch}
        fetched = await fetch([url for url, _, _ in batch], kinds=kinds)

        by_url = {p["url"]: p for p in fetched}
        for url, kind, depth in batch:
            page = by_url.get(url)
            if page is None:      # a fetcher that redirected or dropped the URL
                continue
            page["kind"] = kind
            page["depth"] = depth
            depth_reached = max(depth_reached, depth)
            pages.append(page)
            if kind == "page" and page.get("success") and depth < frontier.max_depth:
                frontier.add_links(extract_links(page.get("html", ""), url), depth)

    stats = frontier.stats()
    stats["depth_reached"] = depth_reached
    return pages, stats
