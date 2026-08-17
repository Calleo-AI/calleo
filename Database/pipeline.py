"""
pipeline.py — Shared fetch + page-to-chunks assembly for create_db.py / update_db.py.

crawl_pages() fetches full-page HTML with httpx — no browser needed, the site
is fully server-rendered (verified: tuition figures appear in raw HTML). It also
fetches linked documents (PDF/DOCX) down a separate streaming path, so a 20 MB
handbook never lands in memory as a decoded string.
build_chunks() turns fetched pages into ChromaDB-ready chunks using
extraction.py + chunking.py, plus documents.py and images.py for the two
non-HTML record kinds.
"""
import sys
from pathlib import Path

import discovery
import documents
import images
from chunking import chunk_page
from extraction import MIN_CHARS, extract_content, extract_title

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from site_config import DOCUMENT_MAX_BYTES, INDEX_IMAGES, USER_AGENT

CONCURRENCY = 4          # polite parallelism for a small site
REQUEST_DELAY = 0.3      # seconds between requests per worker
RETRIES = 3

ERR_TOO_LARGE = "too large"
ERR_UNSUPPORTED = "unsupported content-type"

_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


def _blank_page(url, kind="page", success=False, error="", html="", content=b""):
    return {"url": url, "html": html, "fit_markdown": "", "success": success,
            "error": error, "kind": kind, "content": content, "content_type": ""}


async def crawl_pages(urls, kinds=None):
    """Fetch all urls; returns list of dicts {url, html, fit_markdown, success, error,
    kind, content, content_type}.

    `kinds` is an optional {url: "page"|"document"} map from the frontier. URLs
    it does not cover are classified by extension, so single-argument callers
    (update_db.py, the tests) keep working and a stored .pdf source is still
    refreshed down the document path.

    4xx responses are reported as "HTTP 4xx" errors so build_chunks classifies
    them as dead_url (the sitemap lists dead pages); 5xx/network errors retry
    with backoff and end up as fetch_failed.
    """
    import asyncio

    import httpx

    kinds = kinds or {}
    sem = asyncio.Semaphore(CONCURRENCY)

    async def fetch_document(client, url):
        """Streamed so an oversized file is abandoned instead of buffered."""
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                return _blank_page(url, "document", error=f"HTTP {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > DOCUMENT_MAX_BYTES:
                return _blank_page(url, "document",
                                   error=f"{ERR_TOO_LARGE}: {int(declared)} bytes")
            body = bytearray()
            async for piece in resp.aiter_bytes():
                body.extend(piece)
                # Enforced on the accumulator too: Content-Length can be absent or wrong.
                if len(body) > DOCUMENT_MAX_BYTES:
                    return _blank_page(url, "document",
                                       error=f"{ERR_TOO_LARGE}: over {DOCUMENT_MAX_BYTES} bytes")
            page = _blank_page(url, "document", success=True, content=bytes(body))
            page["content_type"] = resp.headers.get("content-type", "")
            return page

    async def fetch_page(client, url):
        resp = await client.get(url)
        if resp.status_code >= 400:
            return _blank_page(url, error=f"HTTP {resp.status_code}")
        content_type = resp.headers.get("content-type", "")
        base = content_type.split(";")[0].strip().lower()
        if base and not base.startswith(_HTML_CONTENT_TYPES):
            # A sitemap entry pointing at a video or a zip: refuse it rather
            # than decode megabytes of binary into a str.
            return _blank_page(url, error=f"{ERR_UNSUPPORTED}: {base}")
        page = _blank_page(url, success=True, html=resp.text)
        page["content_type"] = content_type
        return page

    async def fetch(client, url):
        kind = kinds.get(url) or ("document" if discovery.is_document(url) else "page")
        handler = fetch_document if kind == "document" else fetch_page
        async with sem:
            error = "unknown"
            for attempt in range(RETRIES):
                try:
                    result = await handler(client, url)
                    if result["error"].startswith(("HTTP 429", "HTTP 503")):
                        error = result["error"]
                        await asyncio.sleep(2 ** attempt * 2)
                        continue
                    await asyncio.sleep(REQUEST_DELAY)
                    return result
                except httpx.HTTPError as e:
                    error = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(2 ** attempt)
            return _blank_page(url, kind, error=error)

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
        label = "DOC " if page.get("kind") == "document" else ""
        print(f"  [{status}] {label}{page['url']} {page['error']}".rstrip(), flush=True)
    return pages


def _failed_status(error):
    if error.startswith("HTTP 4"):
        return "dead_url"       # sitemap rot, not a crawl failure
    if error.startswith(ERR_TOO_LARGE):
        return "too_large"
    if error.startswith(ERR_UNSUPPORTED):
        return "unsupported_type"
    return "fetch_failed"


def _stat(url, status, kind="page", strategy="", chars=0, chunks=0, error="", imgs=0):
    return {"url": url, "status": status, "kind": kind, "strategy": strategy,
            "chars": chars, "chunks": chunks, "error": error, "images": imgs}


def build_chunks(pages, index_images=None):
    """Extract + chunk fetched pages, documents and images.

    Returns (all_chunks, page_stats):
      all_chunks: [{"id", "text", "metadata"}] — content chunks in page order,
                  image chunks appended last (cross-page dedupe needs them all).
      page_stats: [{"url", "status", "kind", "strategy", "chars", "chunks",
                    "error", "images"}]
        status: ok | thin | dead_url | fetch_failed | too_large |
                unsupported_type | extract_failed
    """
    want_images = INDEX_IMAGES if index_images is None else index_images

    all_chunks, page_stats, image_records = [], [], []
    for page in pages:
        url = page["url"]
        kind = page.get("kind", "page")
        if not page["success"]:
            error = page.get("error", "")
            page_stats.append(_stat(url, _failed_status(error), kind, error=error))
            continue

        if kind == "document":
            page_stats.append(_document_stat(page, url, all_chunks))
            continue

        text, strategy = extract_content(page["html"], url, page.get("fit_markdown", ""))
        title = extract_title(page["html"], url)

        # Images are harvested from the raw HTML regardless of how the text
        # extraction went — a page that is thin on prose can still be a gallery.
        page_images = images.harvest(page["html"], url, title) if want_images else []

        if "page or file you requested does not exist" in text.lower():
            # Soft-404: Blackbaud error page served with a 200 status.
            page_stats.append(_stat(url, "dead_url", kind, strategy, len(text),
                                    error="soft 404"))
            continue
        image_records.extend(page_images)
        if len(text) < MIN_CHARS:
            page_stats.append(_stat(url, "thin", kind, strategy, len(text)))
            continue

        chunks = chunk_page(text, title, url)
        for j, c in enumerate(chunks):
            meta = dict(c["metadata"])
            meta["extractor"] = strategy
            meta["kind"] = "page"
            all_chunks.append({"id": f"{url}_chunk_{j}", "text": c["text"], "metadata": meta})
        page_stats.append(_stat(url, "ok", kind, strategy, len(text), len(chunks)))

    image_chunks, per_page = images.build_chunks(image_records)
    all_chunks.extend(image_chunks)
    for stat in page_stats:
        stat["images"] = per_page.get(stat["url"], 0)
    return all_chunks, page_stats


def _document_stat(page, url, all_chunks):
    """Extract + chunk one fetched document, appending to all_chunks. Returns its stat."""
    try:
        text, strategy = documents.extract_document(
            page.get("content", b""), url, page.get("content_type", "")
        )
    except RuntimeError as e:
        # A missing optional dependency must cost us one document, not the rebuild.
        return _stat(url, "extract_failed", "document", error=str(e))
    except Exception as e:
        return _stat(url, "extract_failed", "document",
                     error=f"{type(e).__name__}: {e}")

    if strategy == "unsupported":
        return _stat(url, "unsupported_type", "document", error=ERR_UNSUPPORTED)
    if len(text) < MIN_CHARS:
        # Usually a scanned PDF: no text layer, nothing to embed. Reported, not indexed.
        return _stat(url, "thin", "document", strategy, len(text))

    title = documents.document_title(url)
    chunks = chunk_page(text, title, url)
    for j, c in enumerate(chunks):
        meta = dict(c["metadata"])
        meta["extractor"] = strategy
        meta["kind"] = "document"
        all_chunks.append({"id": f"{url}_chunk_{j}", "text": c["text"], "metadata": meta})
    return _stat(url, "ok", "document", strategy, len(text), len(chunks))


def setup_windows_event_loop():
    """Kept for the script entry points; httpx works on any loop policy."""
    import asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
