"""
delete_chunk.py — Delete one or more chunks from ChromaDB by ID.

Usage:
    python delete_chunk.py <chunk_id>
    python delete_chunk.py <chunk_id_1> <chunk_id_2> ...
    python delete_chunk.py <chunk_id> --collection my_collection
    python delete_chunk.py <chunk_id> --dry-run
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_utils import get_chroma_db
from dotenv import load_dotenv

load_dotenv()

DEFAULT_COLLECTION = "full_database"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Delete one or more chunks from ChromaDB by chunk ID."
    )
    parser.add_argument(
        "chunk_ids",
        nargs="+",
        metavar="CHUNK_ID",
        help="One or more chunk IDs to delete.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        metavar="NAME",
        help=f"ChromaDB collection to target (default: {DEFAULT_COLLECTION}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without making any changes.",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    collection = get_chroma_db(args.collection)

    # Verify each ID exists before attempting deletion
    result = collection.get(ids=args.chunk_ids, include=["metadatas", "documents"])
    found_ids = result.get("ids", [])
    found_docs = result.get("documents", [])
    found_meta = result.get("metadatas", [])

    not_found = [cid for cid in args.chunk_ids if cid not in found_ids]

    print(f"\nCollection : {args.collection}")
    print(f"Requested  : {len(args.chunk_ids)} chunk(s)")
    print(f"Found      : {len(found_ids)} chunk(s)")
    if not_found:
        print(f"Not found  : {len(not_found)} chunk(s)")
        for cid in not_found:
            print(f"  - {cid}")

    if not found_ids:
        print("\nNothing to delete.")
        return

    print("\nChunks to delete:")
    for cid, doc, meta in zip(found_ids, found_docs, found_meta):
        preview = (doc or "")[:120].replace("\n", " ")
        print(f"  id      : {cid}")
        print(f"  source  : {(meta or {}).get('source', 'UNKNOWN')}")
        print(f"  preview : {preview!r}")
        print()

    if args.dry_run:
        print("[DRY RUN] No changes made.")
        return

    collection.delete(ids=found_ids)
    print(f"Deleted {len(found_ids)} chunk(s). Collection now has {collection.count()} chunks.")


if __name__ == "__main__":
    main()
