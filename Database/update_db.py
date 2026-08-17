"""
update_db.py — Refresh ChromaDB entries for specific URLs (or all stored URLs).

Uses the same deterministic extraction + chunking pipeline as create_db.py.
Crawl-first semantics: existing chunks for a URL are only deleted after a
successful re-crawl, so a fetch failure never loses data.

Two things to know:
  * Refreshing a URL is source-atomic — every chunk stored under that source is
    replaced, which for a page includes its image chunks. build_chunks reads
    site_config.INDEX_IMAGES, so a refresh keeps whatever the last build did;
    flip that setting and the next refresh strips images from every page it
    touches.
  * A document URL (.pdf/.docx) refreshes exactly like a page — pipeline routes
    it by extension. But this script never *discovers* anything: new pages and
    newly linked documents only arrive via create_db.py's link crawl.

Usage:
    python update_db.py                      # refresh every URL in the collection
    python update_db.py URL [URL ...]        # refresh only the given URL(s)
    python update_db.py --dry-run
    python update_db.py --collection NAME
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from db_utils import get_chroma_db
from discovery import normalize_url
from pipeline import build_chunks, crawl_pages, setup_windows_event_loop

DEFAULT_COLLECTION = "full_database"
EMBED_BATCH = 20
EMBED_SLEEP = 12  # seconds between batches: Gemini embedding quota is 100 req/min


def get_stored_urls(collection):
    """All distinct http(s) source URLs in the collection (manual entries skipped)."""
    result = collection.get(include=["metadatas"])
    urls = []
    for meta in result.get("metadatas") or []:
        src = (meta or {}).get("source", "")
        if src.startswith("http") and src not in urls:
            urls.append(src)
    return urls


async def refresh_urls(collection, urls, dry_run=False):
    """Re-crawl urls and replace their chunks. Returns (chunks_removed, chunks_added)."""
    urls = [normalize_url(u) for u in urls]
    pages = await crawl_pages(urls)
    all_chunks, page_stats = build_chunks(pages)

    by_source = {}
    for c in all_chunks:
        by_source.setdefault(c["metadata"]["source"], []).append(c)

    total_removed = total_added = 0
    for stat in page_stats:
        url = stat["url"]
        existing = collection.get(where={"source": url}, include=["metadatas"])
        existing_ids = existing.get("ids", [])

        if dry_run:
            print(f"  [DRY-RUN] {url}: {stat['status']} — would replace "
                  f"{len(existing_ids)} chunk(s) with {stat['chunks']}")
            continue

        if stat["status"] != "ok":
            print(f"  [SKIP] {url}: {stat['status']} {stat.get('error', '')} "
                  f"— existing {len(existing_ids)} chunk(s) kept")
            continue

        if existing_ids:
            collection.delete(ids=existing_ids)
            total_removed += len(existing_ids)

        new_chunks = by_source.get(url, [])
        for i in range(0, len(new_chunks), EMBED_BATCH):
            batch = new_chunks[i:i + EMBED_BATCH]
            collection.add(
                ids=[c["id"] for c in batch],
                documents=[c["text"] for c in batch],
                metadatas=[c["metadata"] for c in batch],
            )
            if i + EMBED_BATCH < len(new_chunks):
                await asyncio.sleep(EMBED_SLEEP)
        total_added += len(new_chunks)
        print(f"  [OK] {url}: -{len(existing_ids)} +{len(new_chunks)} chunk(s) "
              f"[{stat['strategy']}]")
    return total_removed, total_added


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Refresh ChromaDB entries for specific URLs (or all stored URLs)."
    )
    parser.add_argument("urls", nargs="*", metavar="URL",
                        help="URLs to refresh. Omit to refresh all URLs in the collection.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would change without writing.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, metavar="NAME")
    return parser.parse_args(argv)


async def run(args):
    collection = get_chroma_db(args.collection)
    urls = args.urls or get_stored_urls(collection)
    if not urls:
        print("No URLs found in the collection. Nothing to update.")
        return 0
    print(f"\nURLs to {'preview' if args.dry_run else 'refresh'}: {len(urls)}\n")
    removed, added = await refresh_urls(collection, urls, dry_run=args.dry_run)
    print("\n" + "=" * 60)
    print(f"  Chunks removed : {removed}")
    print(f"  Chunks added   : {added}")
    print("=" * 60)
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_windows_event_loop()
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
