"""
query_chunks.py — Print all chunks stored in ChromaDB for a given URL.

Usage:
    python query_chunks.py <URL>
    python query_chunks.py <URL> --collection my_collection
    python query_chunks.py <URL> --no-content   # show IDs and metadata only
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
        description="Show all chunks stored in ChromaDB for a specific page URL."
    )
    parser.add_argument("url", metavar="URL", help="Source URL to query.")
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        metavar="NAME",
        help=f"ChromaDB collection to query (default: {DEFAULT_COLLECTION}).",
    )
    parser.add_argument(
        "--no-content",
        action="store_true",
        help="Only show chunk IDs and metadata, not the text content.",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    collection = get_chroma_db(args.collection)

    include = ["metadatas", "documents"] if not args.no_content else ["metadatas"]
    result = collection.get(where={"source": args.url}, include=include)

    ids = result.get("ids", [])
    metadatas = result.get("metadatas", [])
    documents = result.get("documents", [None] * len(ids))

    if not ids:
        print(f"No chunks found for: {args.url}")
        return

    print(f"\nCollection : {args.collection}")
    print(f"URL        : {args.url}")
    print(f"Chunks     : {len(ids)}")
    print("=" * 60)

    for i, (chunk_id, meta, doc) in enumerate(zip(ids, metadatas, documents)):
        print(f"\n[Chunk {i + 1}]  id={chunk_id}")
        print(f"  metadata : {meta}")
        if doc is not None:
            print(f"  content  :\n{doc}")
        print("-" * 60)


if __name__ == "__main__":
    main()
