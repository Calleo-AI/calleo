"""
Load Text Files into ChromaDB

Reads specific .txt files (e.g., school_profile.txt) and stores
their contents in the 'enrollment_info' ChromaDB collection.
"""

import os
import sys
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv

# llm_client lives at the repo root — the single seam for all LLM/embedding calls.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import llm_client

load_dotenv()

# Text files to ingest (relative to this script's directory)
TEXT_FILES = [
    "school_profile.txt",
]


def get_chroma_db(name):
    """Get or create a ChromaDB collection with Google embeddings."""
    chroma_client = chromadb.PersistentClient(path=os.environ.get("CHROMA_DB_PATH"))
    return chroma_client.get_or_create_collection(
        name=name, embedding_function=llm_client.get_embedding_function()
    )


def load_text_files(db):
    """Read each text file, split into chunks, and add to ChromaDB."""
    script_dir = os.path.dirname(os.path.abspath(__file__))

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        length_function=len
    )

    doc_list = []
    id_list = []
    metadata_list = []

    for filename in TEXT_FILES:
        filepath = os.path.join(script_dir, filename)

        if not os.path.exists(filepath):
            print(f"[WARN] File not found, skipping: {filepath}")
            continue

        print(f"[Reading] {filename}")
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            print(f"[WARN] File is empty, skipping: {filename}")
            continue

        # Split content into chunks
        chunks = text_splitter.split_text(content)
        print(f"[OK] Split into {len(chunks)} chunk(s).")

        for j, chunk in enumerate(chunks):
            doc_list.append(chunk)
            chunk_id = f"{filename}_chunk_{j}"
            id_list.append(chunk_id)
            metadata_list.append({"source": filename})

    # Add all chunks to ChromaDB
    if doc_list:
        print(f"\nAdding {len(doc_list)} total document chunk(s) to ChromaDB...")
        db.add(
            ids=id_list,
            documents=doc_list,
            metadatas=metadata_list
        )
        print("Done.")
    else:
        print("No documents to add.")


def main():
    db = get_chroma_db("enrollment_info")
    load_text_files(db)
    print(f"Total documents in enrollment_info: {db.count()}")


if __name__ == "__main__":
    main()
