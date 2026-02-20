import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from auric.core.config import AURIC_ROOT
from .vector_store import VectorStore

logger = logging.getLogger("auric.memory.chroma")

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError as e:
    CHROMADB_AVAILABLE = False
    CHROMADB_ERROR = str(e)
except Exception as e:
    # Catching config errors from Pydantic which happen at import time
    CHROMADB_AVAILABLE = False
    CHROMADB_ERROR = str(e)

class ChromaStore(VectorStore):
    """
    ChromaDB implementation of the VectorStore interface.
    """

    def __init__(self, collection_name: str = "auric_memory", persistence_path: Optional[Path] = None):
        if not CHROMADB_AVAILABLE:
            logger.error(f"ChromaDB is not available: {CHROMADB_ERROR}")
            raise RuntimeError(f"ChromaDB is not available: {CHROMADB_ERROR}")

        if persistence_path is None:
            persistence_path = AURIC_ROOT / "chroma_db"
        
        self.persistence_path = persistence_path
        self.collection_name = collection_name
        
        try:
            # Initialize ChromaDB Client
            # Using PersistentClient for local storage
            self.client = chromadb.PersistentClient(path=str(self.persistence_path))
            
            # Get or create collection
            self.collection = self.client.get_or_create_collection(name=self.collection_name)
            logger.info(f"ChromaStore initialized at {self.persistence_path}")
        except Exception as e:
             logger.error(f"Failed to initialize ChromaDB client: {e}")
             raise

    def upsert(self, id: str, content: str, metadata: Dict[str, Any], embedding: List[float]) -> None:
        """
        Insert or update a document.
        """
        try:
            self.collection.upsert(
                ids=[id],
                documents=[content],
                metadatas=[metadata],
                embeddings=[embedding]
            )
        except Exception as e:
            logger.error(f"Failed to upsert {id}: {e}")
            raise

    def batch_upsert(self, ids: List[str], contents: List[str], metadatas: List[Dict[str, Any]], embeddings: List[List[float]]) -> None:
        """
        Insert or update multiple documents in a single call.
        """
        try:
            self.collection.upsert(
                ids=ids,
                documents=contents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        except Exception as e:
            logger.error(f"Failed to batch upsert {len(ids)} documents: {e}")
            raise

    def search(self, query_embedding: List[float], n_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search using query embedding.
        """
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"]
            )
            
            # Format results
            # Chroma returns lists of lists (batch format)
            formatted_results = []
            if results["ids"] and len(results["ids"]) > 0:
                ids = results["ids"][0]
                documents = results["documents"][0] if results["documents"] else []
                metadatas = results["metadatas"][0] if results["metadatas"] else []
                distances = results["distances"][0] if results["distances"] else []

                for i in range(len(ids)):
                    formatted_results.append({
                        "id": ids[i],
                        "content": documents[i],
                        "metadata": metadatas[i] if i < len(metadatas) else {},
                        "distance": distances[i] if i < len(distances) else 0.0
                    })
            
            return formatted_results

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def delete(self, id: str) -> None:
        """
        Delete by ID.
        """
        try:
            self.collection.delete(ids=[id])
        except Exception as e:
            logger.error(f"Failed to delete {id}: {e}")

    def delete_by_metadata(self, filter: Dict[str, Any]) -> None:
        """
        Delete by metadata filter.
        """
        try:
            self.collection.delete(where=filter)
        except Exception as e:
            logger.error(f"Failed to delete by metadata {filter}: {e}")

    def wipe(self) -> None:
        """
        Wipe the collection.
        """
        try:
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.get_or_create_collection(name=self.collection_name)
            logger.warning("ChromaStore wiped.")
        except Exception as e:
            logger.error(f"Failed to wipe store: {e}")
