"""
pipeline.py — Shared fetch + page-to-chunks assembly for create_db.py / update_db.py.

crawl_pages() fetches full-page HTML with httpx — no browser needed, the site
is fully server-rendered (verified: tuition figures appear in raw HTML).
build_chunks() turns fetched pages into ChromaDB-ready chunks using
extraction.py + chunking.py.
"""
import sys
from pathlib import Path

from chunking import chunk_page
from extraction import MIN_CHARS, extract_content, extract_title

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from school_config import USER_AGENT

CONCURRENCY = 4          # polite parallelism for a school site
REQUEST_DELAY = 0.3      # seconds between requests per worker
RETRIES = 3


async def crawl_pages(urls):
    """Fetch all urls; returns list of dicts {url, html, fit_markdown, success, error}.

    4xx responses are reported as "HTTP 4xx" errors so build_chunks classifies
    them as dead_url (the sitemap lists dead pages); 5xx/network errors retry
    with backoff and end up as fetch_failed.
    """
    import asyncio

    import httpx

    sem = asyncio.Semaphore(CONCURRENCY)

    async def fetch(client, url):
        async with sem:
            error = "unknown"
            for attempt in range(RETRIES):
                try:
                    resp = await client.get(url)
                    if resp.status_code in (429, 503):
                        error = f"HTTP {resp.status_code}"
                        await asyncio.sleep(2 ** attempt * 2)
                        continue
                    if resp.status_code >= 400:
                        return {"url": url, "html": "", "fit_markdown": "",
                                "success": False, "error": f"HTTP {resp.status_code}"}
                    await asyncio.sleep(REQUEST_DELAY)
                    return {"url": url, "html": resp.text, "fit_markdown": "",
                            "success": True, "error": ""}
                except httpx.HTTPError as e:
                    error = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(2 ** attempt)
            return {"url": url, "html": "", "fit_markdown": "",
                    "success": False, "error": error}

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        pages = list(await asyncio.gather(*(fetch(client, u) for u in urls)))

    for page in pages:
        if page["success"]:
            status = "OK"
        elif page["error"].startswith("HTTP 4"):
            status = "DEAD"
        else:
            status = "FAIL"
        print(f"  [{status}] {page['url']} {page['error']}".rstrip(), flush=True)
    return pages


def build_chunks(pages):
    """Extract + chunk fetched pages.

    Returns (all_chunks, page_stats):
      all_chunks: [{"id", "text", "metadata"}]
      page_stats: [{"url", "status": ok|thin|dead_url|fetch_failed, "strategy",
                    "chars", "chunks", "error"}]
    """
    all_chunks, page_stats = [], []
    for page in pages:
        url = page["url"]
        if not page["success"]:
            error = page.get("error", "")
            status = "dead_url" if error.startswith("HTTP 4") else "fetch_failed"
            page_stats.append({"url": url, "status": status,
                               "strategy": "", "chars": 0, "chunks": 0,
                               "error": error})
            continue
        text, strategy = extract_content(page["html"], url, page.get("fit_markdown", ""))
        if "page or file you requested does not exist" in text.lower():
            # Soft-404: Blackbaud error page served with a 200 status.
            page_stats.append({"url": url, "status": "dead_url",
                               "strategy": strategy, "chars": len(text), "chunks": 0,
                               "error": "soft 404"})
            continue
        if len(text) < MIN_CHARS:
            page_stats.append({"url": url, "status": "thin",
                               "strategy": strategy, "chars": len(text), "chunks": 0,
                               "error": ""})
            continue
        title = extract_title(page["html"], url)
        chunks = chunk_page(text, title, url)
        for j, c in enumerate(chunks):
            meta = dict(c["metadata"])
            meta["extractor"] = strategy
            all_chunks.append({"id": f"{url}_chunk_{j}", "text": c["text"], "metadata": meta})
        page_stats.append({"url": url, "status": "ok", "strategy": strategy,
                           "chars": len(text), "chunks": len(chunks), "error": ""})
    return all_chunks, page_stats


def setup_windows_event_loop():
    """Kept for the script entry points; httpx works on any loop policy."""
    import asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
