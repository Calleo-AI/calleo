import os
import sys
import chromadb
import time
from dotenv import load_dotenv

# llm_client lives at the repo root — the single seam for all LLM/embedding calls.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import llm_client

load_dotenv()

def verify_logs():
    try:
        chroma_client = chromadb.PersistentClient(path=os.environ.get("CHROMA_DB_PATH"))
        collection = chroma_client.get_collection(
            name="full_database_conversations",
            embedding_function=llm_client.get_embedding_function(),
        )
        
        count = collection.count()
        print(f"Total conversations logged: {count}")
        
        # Get all documents to find the latest one (not efficient for large DBs but fine for verification)
        all_docs = collection.get()
        if all_docs['ids']:
            last_index = -1
            print("\nMost recent log entry:")
            print(all_docs['documents'][last_index])
            print(all_docs['metadatas'][last_index])
            return True
        else:
            print("No conversations found in the database.")
            return False
            
    except Exception as e:
        print(f"Error verifying logs: {e}")
        return False

if __name__ == "__main__":
    verify_logs()
