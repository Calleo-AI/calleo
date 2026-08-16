"""
insert_content.py — Paste content and a URL to encode it into ChromaDB.

Usage:
    python insert_content.py
    python insert_content.py --collection my_collection

The script will prompt for:
  1. The source URL
  2. The content to encode (paste, then type END on its own line and press Enter)

Any existing chunks for that URL are deleted before inserting the new ones.
"""
import os
import sys
import argparse
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_utils import get_chroma_db
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv

load_dotenv()

DEFAULT_COLLECTION = "full_database"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Paste content + URL and encode it into ChromaDB."
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        metavar="NAME",
        help=f"ChromaDB collection to target (default: {DEFAULT_COLLECTION}).",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    # ── Get URL ───────────────────────────────────────────────────────────────
    print(f"\nCollection: {args.collection}")
    print("-" * 60)
    url = input("Source URL: ").strip()
    if not url:
        print("No URL provided. Exiting.")
        return

    # ── Get content ───────────────────────────────────────────────────────────
    print("\nPaste your content below.")
    print('When done, type END on its own line and press Enter.')
    print("-" * 60)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)

    content = "\n".join(lines).strip()
    if not content:
        print("No content provided. Exiting.")
        return

    print(f"\nContent received: {len(content)} characters")

    # ── Connect to DB ─────────────────────────────────────────────────────────
    collection = get_chroma_db(args.collection)

    # ── Delete existing chunks ────────────────────────────────────────────────
    existing = collection.get(where={"source": url}, include=["metadatas"])
    existing_ids = existing.get("ids", [])
    if existing_ids:
        collection.delete(ids=existing_ids)
        print(f"Deleted {len(existing_ids)} existing chunk(s) for this URL")
    else:
        print("No existing chunks found for this URL")

    # ── Split and insert ──────────────────────────────────────────────────────
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        length_function=len,
    )
    chunks = splitter.split_text(content)
    print(f"Split into {len(chunks)} chunk(s)")

    ids = [f"{url}_chunk_{i}" for i in range(len(chunks))]
    metadatas = [{"source": url} for _ in chunks]

    batch_size = 20
    for i in range(0, len(chunks), batch_size):
        collection.add(
            ids=ids[i:i + batch_size],
            documents=chunks[i:i + batch_size],
            metadatas=metadatas[i:i + batch_size],
        )
        if i + batch_size < len(chunks):
            print(f"  Batch {i // batch_size + 1} inserted, waiting for rate limit...")
            await asyncio.sleep(12)

    print(f"\nDone. Inserted {len(chunks)} chunk(s) for:")
    print(f"  {url}")
    print(f"Collection '{args.collection}' now has {collection.count()} total chunks.")


if __name__ == "__main__":
    asyncio.run(main())
