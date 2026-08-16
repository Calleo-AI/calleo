import os
import chromadb
from dotenv import load_dotenv

load_dotenv()

# Connect to your existing database
chroma_client = chromadb.PersistentClient(path=os.environ.get("CHROMA_DB_PATH"))

# Delete the old collection
try:
    chroma_client.delete_collection(name="full_database_conversations")
    print("✓ Successfully deleted 'enrollment_info' collection")
except Exception as e:
    print(f"Error deleting collection: {e}")

# Verify it's gone
collections = chroma_client.list_collections()
print(f"\nRemaining collections: {[c.name for c in collections]}")

print("\nYou can now run your database.py script with the new extraction prompt!")