import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from auric.core.config import AURIC_ROOT
from .vector_store import VectorStore

logger = logging.getLogger("auric.memory.chroma")

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except Exception as e:
    CHROMADB_AVAILABLE = False
    CHROMADB_ERROR = str(e)


class ChromaStore(VectorStore):
    """ChromaDB implementation of the VectorStore interface."""

    def __init__(self, collection_name: str = "auric_memory", persistence_path: Optional[Path] = None):
        if not CHROMADB_AVAILABLE:
            logger.error(f"ChromaDB is not available: {CHROMADB_ERROR}")
            raise RuntimeError(f"ChromaDB is not available: {CHROMADB_ERROR}")

        self.persistence_path = persistence_path or (AURIC_ROOT / "chroma_db")
        self.collection_name = collection_name

        try:
            self.client = chromadb.PersistentClient(path=str(self.persistence_path))
            self.collection = self.client.get_or_create_collection(name=self.collection_name)
            logger.info(f"ChromaStore initialized at {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB client: {e}")
            raise

    def upsert(self, id: str, content: str, metadata: Dict[str, Any], embedding: List[float]) -> None:
        """Insert or update a single document."""
        try:
            self.collection.upsert(
                ids=[id], documents=[content],
                metadatas=[metadata], embeddings=[embedding],
            )
        except Exception as e:
            logger.error(f"Failed to upsert {id}: {e}")
            raise

    def batch_upsert(self, ids: List[str], contents: List[str],
                     metadatas: List[Dict[str, Any]], embeddings: List[List[float]]) -> None:
        """Insert or update multiple documents in a single call."""
        try:
            self.collection.upsert(
                ids=ids, documents=contents,
                metadatas=metadatas, embeddings=embeddings,
            )
        except Exception as e:
            logger.error(f"Failed to batch upsert {len(ids)} documents: {e}")
            raise

    def search(self, query_embedding: List[float], n_results: int = 5) -> List[Dict[str, Any]]:
        """Search for similar documents and return formatted results."""
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )

            if not results["ids"]:
                return []

            ids = results["ids"][0]
            documents = results["documents"][0] if results["documents"] else []
            metadatas = results["metadatas"][0] if results["metadatas"] else []
            distances = results["distances"][0] if results["distances"] else []

            return [
                {
                    "id": doc_id,
                    "content": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                    "distance": distances[i] if i < len(distances) else 0.0,
                }
                for i, doc_id in enumerate(ids)
            ]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def delete(self, id: str) -> None:
        """Delete a document by ID."""
        try:
            self.collection.delete(ids=[id])
        except Exception as e:
            logger.error(f"Failed to delete {id}: {e}")

    def delete_by_metadata(self, filter: Dict[str, Any]) -> None:
        """Delete documents matching a metadata filter."""
        try:
            self.collection.delete(where=filter)
        except Exception as e:
            logger.error(f"Failed to delete by metadata {filter}: {e}")

    def wipe(self) -> None:
        """Drop and recreate the collection."""
        try:
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.get_or_create_collection(name=self.collection_name)
            logger.warning("ChromaStore wiped.")
        except Exception as e:
            logger.error(f"Failed to wipe store: {e}")
