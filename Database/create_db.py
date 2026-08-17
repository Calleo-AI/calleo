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
    python create_db.py --no-links       # sitemap only, don't follow on-page links
    python create_db.py --no-images --no-documents

The crawl starts from the sitemap and follows same-site links outward (see
frontier.py), fetching linked PDFs/DOCX as well and indexing page images as
text records. Run --dry-run first after changing any of those knobs: it prints
the chunk mix and the embedding-time estimate without touching the DB.

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

import site_config
from db_utils import delete_collection, get_chroma_db
from discovery import get_site_urls
from frontier import crawl_site
from pipeline import build_chunks, crawl_pages, setup_windows_event_loop
from snapshot_db import create_snapshot

DEFAULT_COLLECTION = "full_database"
EMBED_BATCH = 20
EMBED_SLEEP = 12  # seconds between batches: Gemini embedding quota is 100 req/min

MIN_PAGE_SUCCESS_RATE = 0.90
MIN_TOTAL_CHUNKS = 200
MAX_IMAGE_CHUNK_RATIO = 0.35   # images must not become the bulk of the knowledge base
KEY_PAGE_CHECKS = site_config.KEY_PAGE_CHECKS

# Statuses that describe the resource rather than a crawl problem — they never
# count against the page success rate.
NON_COUNTABLE_STATUSES = {"dead_url", "too_large", "unsupported_type"}


def content_chunks(all_chunks):
    """Page-prose chunks only — image and document chunks excluded.

    Every quality gate measures these. Padding the total with hundreds of image
    chunks must never let a collapse in text extraction slip through the
    MIN_TOTAL_CHUNKS floor, and an image's alt text must never be what
    satisfies a key-page needle check.
    """
    return [c for c in all_chunks if c["metadata"].get("kind", "page") == "page"]


def chunk_mix(all_chunks):
    """{'page': n, 'document': n, 'image': n} — the cost and quality breakdown."""
    mix = {"page": 0, "document": 0, "image": 0}
    for c in all_chunks:
        kind = c["metadata"].get("kind", "page")
        mix[kind] = mix.get(kind, 0) + 1
    return mix


def embed_minutes(chunk_count):
    """Wall-clock estimate for write_staging: one EMBED_SLEEP between batches."""
    batches = max(0, -(-chunk_count // EMBED_BATCH) - 1)
    return round(batches * EMBED_SLEEP / 60, 1)

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rebuild_report.txt")


def validate(page_stats, all_chunks, subset=False):
    """Return a list of validation error strings (empty list == pass)."""
    errors = []
    # Documents are graded separately: a site with 30 encrypted PDFs must not
    # block a rebuild of 200 healthy pages.
    html_stats = [p for p in page_stats if p.get("kind", "page") == "page"]
    ok = [p for p in html_stats if p["status"] == "ok"]
    # dead_url pages are sitemap rot (HTTP 404 / soft-404), not crawl failures —
    # they don't count against the success rate.
    countable = [p for p in html_stats if p["status"] not in NON_COUNTABLE_STATUSES]
    rate = len(ok) / len(countable) if countable else 0.0
    if rate < MIN_PAGE_SUCCESS_RATE:
        errors.append(
            f"Page success rate {rate:.0%} is below {MIN_PAGE_SUCCESS_RATE:.0%} "
            f"({len(ok)}/{len(countable)} crawlable pages)"
        )

    prose = content_chunks(all_chunks)
    if not subset and len(prose) < MIN_TOTAL_CHUNKS:
        errors.append(f"Only {len(prose)} page chunks produced (< {MIN_TOTAL_CHUNKS})")

    mix = chunk_mix(all_chunks)
    total = sum(mix.values())
    if total and mix["image"] / total > MAX_IMAGE_CHUNK_RATIO:
        errors.append(
            f"Image chunks are {mix['image'] / total:.0%} of the build "
            f"(> {MAX_IMAGE_CHUNK_RATIO:.0%}) — they would crowd out page content "
            f"at retrieval; raise IMAGE_MIN_ALT_CHARS or run --no-images"
        )

    crawled_urls = {p["url"] for p in page_stats}
    by_url = {}
    for c in prose:
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


def _status_counts(stats):
    counts = {}
    for p in stats:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return counts


def write_report(page_stats, all_chunks, errors, kept_foreign=None, path=REPORT_PATH,
                 frontier_stats=None):
    lines = [f"{site_config.SITE_SHORT_NAME} knowledge-base rebuild report", "=" * 60, ""]
    html_stats = [p for p in page_stats if p.get("kind", "page") == "page"]
    doc_stats = [p for p in page_stats if p.get("kind") == "document"]
    strategies = {}
    for p in page_stats:
        if p["status"] == "ok":
            strategies[p["strategy"]] = strategies.get(p["strategy"], 0) + 1

    mix = chunk_mix(all_chunks)
    lines.append(f"Pages: {len(html_stats)}  {_status_counts(html_stats)}")
    if doc_stats:
        lines.append(f"Documents: {len(doc_stats)}  {_status_counts(doc_stats)}")
    if frontier_stats:
        f = frontier_stats
        lines.append(
            f"Link crawl: depth<={f.get('max_depth')} (reached {f.get('depth_reached')}), "
            f"{f.get('seeds', 0)} sitemap seeds + {f.get('link_pages', 0)} discovered pages, "
            f"{f.get('documents', 0)} documents; skipped "
            f"{f.get('skipped_depth', 0)} (depth) / {f.get('skipped_budget', 0)} (budget) / "
            f"{f.get('skipped_policy', 0)} (excluded)"
        )
    images_indexed = sum(p.get("images", 0) for p in page_stats)
    lines.append(f"Images: {mix['image']} chunks from {images_indexed} records")
    lines.append(f"Extraction strategies: {strategies}")
    lines.append(
        f"Total chunks: {len(all_chunks)}  "
        f"(page {mix['page']} / document {mix['document']} / image {mix['image']})"
    )
    lines.append(f"Estimated embedding time: {embed_minutes(len(all_chunks))} min")
    if kept_foreign:
        lines.append("")
        lines.append("Sources kept but NOT refreshed this run (manual entries / failed pages):")
        lines.extend(f"  {s}" for s in sorted(kept_foreign))
    if errors:
        lines.append("")
        lines.append("VALIDATION ERRORS:")
        lines.extend(f"  {e}" for e in errors)
    lines.append("")
    lines.append(f"{'URL':<110}  {'KIND':<9}  {'STATUS':<16}  {'STRATEGY':<12}  "
                 f"{'CHARS':>7}  {'CHUNKS':>6}  {'IMGS':>4}")
    for p in sorted(page_stats, key=lambda x: (x.get("kind", "page"),
                                               x["status"] != "ok", x["url"])):
        lines.append(
            f"{p['url']:<110}  {p.get('kind', 'page'):<9}  {p['status']:<16}  "
            f"{p['strategy']:<12}  {p['chars']:>7}  {p['chunks']:>6}  "
            f"{p.get('images', 0):>4}"
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
                        help="Only process the first N sitemap seeds "
                             "(smoke test; relaxes validation).")
    parser.add_argument("--prune", action="store_true",
                        help="Delete sources that are no longer part of the crawl.")
    parser.add_argument("--force", action="store_true",
                        help="Swap into the live collection even if validation fails.")
    parser.add_argument("--links", action=argparse.BooleanOptionalAction,
                        default=site_config.CRAWL_FOLLOW_LINKS,
                        help="Follow on-page links beyond the sitemap.")
    parser.add_argument("--max-depth", type=int, default=site_config.CRAWL_MAX_DEPTH,
                        metavar="N", help="Link hops away from a sitemap URL.")
    parser.add_argument("--max-link-pages", type=int, default=site_config.CRAWL_MAX_PAGES,
                        metavar="N", help="Cap on link-discovered pages (0 = unlimited).")
    parser.add_argument("--documents", action=argparse.BooleanOptionalAction,
                        default=site_config.CRAWL_DOCUMENTS,
                        help="Fetch and index linked PDF/DOCX files.")
    parser.add_argument("--images", action=argparse.BooleanOptionalAction,
                        default=site_config.INDEX_IMAGES,
                        help="Index page images as text records.")
    return parser.parse_args(argv)


async def run(args):
    print("Discovering URLs from sitemap...")
    try:
        urls = get_site_urls()
    except Exception as e:
        sys.exit(
            f"ERROR: could not fetch the sitemap at {site_config.SITEMAP_URL}: {e}\n"
            "Check SITE_ROOT / SITEMAP_URL in site_config.py - some CMSes "
            "(e.g. Blackbaud) serve the sitemap at /sitemap instead of /sitemap.xml."
        )
    if args.max_pages:
        urls = urls[: args.max_pages]
    print(f"Sitemap seeds: {len(urls)}\n")

    pages, frontier_stats = await crawl_site(
        urls,
        crawl_pages,
        max_depth=args.max_depth if args.links else 0,
        max_pages=args.max_link_pages,
        allow_documents=args.documents,
    )
    print(f"\nFetched {len(pages)} resources: {frontier_stats}")

    all_chunks, page_stats = build_chunks(pages, index_images=args.images)
    errors = validate(page_stats, all_chunks, subset=bool(args.max_pages))

    if args.dry_run:
        write_report(page_stats, all_chunks, errors, frontier_stats=frontier_stats)
        print("\n[DRY-RUN] No database changes made.")
        return 1 if errors else 0

    if errors and not args.force:
        write_report(page_stats, all_chunks, errors, frontier_stats=frontier_stats)
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

    print(f"\nEmbedding {len(all_chunks)} chunks into {staging_name} "
          f"(~{embed_minutes(len(all_chunks))} min)...")
    await write_staging(all_chunks, staging)

    print("\nSwapping staged data into live collection...")
    kept_foreign, removed = swap_into_live(live, staging, prune=args.prune)
    delete_collection(staging_name)

    write_report(page_stats, all_chunks, errors, kept_foreign,
                 frontier_stats=frontier_stats)
    mix = chunk_mix(all_chunks)
    print("\n" + "=" * 60)
    print("REBUILD COMPLETE")
    print(f"  Pages crawled     : {len(page_stats)}")
    print(f"  Chunks added      : {len(all_chunks)} "
          f"(page {mix['page']} / document {mix['document']} / image {mix['image']})")
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
