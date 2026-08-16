"""
create_db.py — One-click full rebuild of the knowledge base from the live website.

Usage:
    python create_db.py                  # full rebuild: snapshot -> crawl -> stage ->
                                         # validate -> swap into full_database
    python create_db.py --dry-run        # crawl + extract + chunk + report, no DB writes
    python create_db.py --max-pages 8    # smoke-test on a subset (relaxed validation)
    python create_db.py --prune          # also delete sources no longer crawled
    python create_db.py --force          # swap even if validation fails (not recommended)
    python create_db.py --collection NAME

The live collection is only modified after the staged build passes validation.
A snapshot is taken first (snapshot_db.py --rollback restores it).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import school_config
from db_utils import delete_collection, get_chroma_db
from discovery import get_site_urls
from pipeline import build_chunks, crawl_pages, setup_windows_event_loop
from snapshot_db import create_snapshot

DEFAULT_COLLECTION = "full_database"
EMBED_BATCH = 20
EMBED_SLEEP = 12  # seconds between batches: Gemini embedding quota is 100 req/min

MIN_PAGE_SUCCESS_RATE = 0.90
MIN_TOTAL_CHUNKS = 200
KEY_PAGE_CHECKS = school_config.KEY_PAGE_CHECKS

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rebuild_report.txt")


def validate(page_stats, all_chunks, subset=False):
    """Return a list of validation error strings (empty list == pass)."""
    errors = []
    ok = [p for p in page_stats if p["status"] == "ok"]
    # dead_url pages are sitemap rot (HTTP 404 / soft-404), not crawl failures —
    # they don't count against the success rate.
    countable = [p for p in page_stats if p["status"] != "dead_url"]
    rate = len(ok) / len(countable) if countable else 0.0
    if rate < MIN_PAGE_SUCCESS_RATE:
        errors.append(
            f"Page success rate {rate:.0%} is below {MIN_PAGE_SUCCESS_RATE:.0%} "
            f"({len(ok)}/{len(countable)} crawlable pages)"
        )
    if not subset and len(all_chunks) < MIN_TOTAL_CHUNKS:
        errors.append(f"Only {len(all_chunks)} chunks produced (< {MIN_TOTAL_CHUNKS})")

    crawled_urls = {p["url"] for p in page_stats}
    by_url = {}
    for c in all_chunks:
        # Check the chunk BODY only — the contextual header always contains the
        # URL/title, which would make any needle check trivially (falsely) pass.
        body = c["text"].split("\n\n", 1)[-1]
        by_url.setdefault(c["metadata"]["source"], []).append(body.lower())
    for url, needle in KEY_PAGE_CHECKS.items():
        if subset and url not in crawled_urls:
            continue
        texts = by_url.get(url, [])
        if not texts:
            errors.append(f"Key page produced no chunks: {url}")
        elif not any(needle in t for t in texts):
            errors.append(f"Key page {url} lacks expected text {needle!r}")
    return errors


async def write_staging(chunks, staging):
    """Add chunks to the staging collection in embedding-quota-friendly batches."""
    total = len(chunks)
    for i in range(0, total, EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        staging.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
        )
        done = min(i + EMBED_BATCH, total)
        print(f"  embedded {done}/{total} chunks", flush=True)
        if done < total:
            await asyncio.sleep(EMBED_SLEEP)


def swap_into_live(live, staging, prune=False):
    """Copy staged docs+embeddings into the live collection (no re-embedding).

    Deletes live chunks whose source was re-crawled (always) and, with
    prune=True, every chunk not present in staging. Other sources — manual
    insert_content.py entries, pages that failed this run — are preserved.
    Returns (kept_foreign_sources, deleted_count).
    """
    staged = staging.get(include=["documents", "metadatas", "embeddings"])
    staged_sources = {m["source"] for m in staged["metadatas"]}

    existing = live.get(include=["metadatas"])
    to_delete, kept_foreign = [], set()
    for cid, meta in zip(existing["ids"], existing["metadatas"]):
        src = (meta or {}).get("source", "")
        if prune or src in staged_sources:
            to_delete.append(cid)
        else:
            kept_foreign.add(src)
    for i in range(0, len(to_delete), 500):
        live.delete(ids=to_delete[i:i + 500])

    raw_emb = staged.get("embeddings")
    embeddings = None
    if raw_emb is not None and len(raw_emb) > 0:
        embeddings = [e.tolist() if hasattr(e, "tolist") else list(e) for e in raw_emb]

    ids = staged["ids"]
    for i in range(0, len(ids), 100):
        live.add(
            ids=ids[i:i + 100],
            documents=staged["documents"][i:i + 100],
            metadatas=staged["metadatas"][i:i + 100],
            embeddings=embeddings[i:i + 100] if embeddings is not None else None,
        )
    return kept_foreign, len(to_delete)


def write_report(page_stats, all_chunks, errors, kept_foreign=None, path=REPORT_PATH):
    lines = [f"{school_config.SCHOOL_SHORT_NAME} knowledge-base rebuild report", "=" * 60, ""]
    counts = {}
    for p in page_stats:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    strategies = {}
    for p in page_stats:
        if p["status"] == "ok":
            strategies[p["strategy"]] = strategies.get(p["strategy"], 0) + 1
    lines.append(f"Pages: {len(page_stats)}  {counts}")
    lines.append(f"Extraction strategies: {strategies}")
    lines.append(f"Total chunks: {len(all_chunks)}")
    if kept_foreign:
        lines.append("")
        lines.append("Sources kept but NOT refreshed this run (manual entries / failed pages):")
        lines.extend(f"  {s}" for s in sorted(kept_foreign))
    if errors:
        lines.append("")
        lines.append("VALIDATION ERRORS:")
        lines.extend(f"  {e}" for e in errors)
    lines.append("")
    lines.append(f"{'URL':<90}  {'STATUS':<12}  {'STRATEGY':<12}  {'CHARS':>6}  {'CHUNKS':>6}")
    for p in sorted(page_stats, key=lambda x: (x["status"] != "ok", x["url"])):
        lines.append(
            f"{p['url']:<90}  {p['status']:<12}  {p['strategy']:<12}  "
            f"{p['chars']:>6}  {p['chunks']:>6}"
        )
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\nReport written to {path}")
    return text


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild the knowledge base from the live website.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, metavar="NAME")
    parser.add_argument("--dry-run", action="store_true",
                        help="Crawl/extract/chunk and report, but write nothing to the DB.")
    parser.add_argument("--max-pages", type=int, default=0, metavar="N",
                        help="Only process the first N URLs (smoke test; relaxes validation).")
    parser.add_argument("--prune", action="store_true",
                        help="Delete sources that are no longer part of the crawl.")
    parser.add_argument("--force", action="store_true",
                        help="Swap into the live collection even if validation fails.")
    return parser.parse_args(argv)


async def run(args):
    print("Discovering URLs from sitemap...")
    try:
        urls = get_site_urls()
    except Exception as e:
        sys.exit(
            f"ERROR: could not fetch the sitemap at {school_config.SITEMAP_URL}: {e}\n"
            "Check SITE_ROOT / SITEMAP_URL in school_config.py - some CMSes "
            "(e.g. Blackbaud) serve the sitemap at /sitemap instead of /sitemap.xml."
        )
    if args.max_pages:
        urls = urls[: args.max_pages]
    print(f"URLs to crawl: {len(urls)}\n")

    pages = await crawl_pages(urls)
    all_chunks, page_stats = build_chunks(pages)
    errors = validate(page_stats, all_chunks, subset=bool(args.max_pages))

    if args.dry_run:
        write_report(page_stats, all_chunks, errors)
        print("\n[DRY-RUN] No database changes made.")
        return 1 if errors else 0

    if errors and not args.force:
        write_report(page_stats, all_chunks, errors)
        print("\nVALIDATION FAILED — live collection untouched:")
        for e in errors:
            print(f"  - {e}")
        return 1

    live = get_chroma_db(args.collection)
    if live.count() > 0:
        print("\nSnapshotting live collection...")
        create_snapshot(live, args.collection)

    staging_name = f"{args.collection}_staging"
    delete_collection(staging_name)  # clear leftovers from any earlier failed run
    staging = get_chroma_db(staging_name)

    print(f"\nEmbedding {len(all_chunks)} chunks into {staging_name}...")
    await write_staging(all_chunks, staging)

    print("\nSwapping staged data into live collection...")
    kept_foreign, removed = swap_into_live(live, staging, prune=args.prune)
    delete_collection(staging_name)

    write_report(page_stats, all_chunks, errors, kept_foreign)
    print("\n" + "=" * 60)
    print("REBUILD COMPLETE")
    print(f"  Pages crawled     : {len(page_stats)}")
    print(f"  Chunks added      : {len(all_chunks)}")
    print(f"  Old chunks removed: {removed}")
    print(f"  Live collection   : {live.count()} chunks total")
    if kept_foreign:
        print(f"  Unrefreshed sources kept: {len(kept_foreign)} (see report; --prune removes)")
    print("=" * 60)
    return 0


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_windows_event_loop()
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
