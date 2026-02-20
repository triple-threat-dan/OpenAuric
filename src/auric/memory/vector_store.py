from abc import ABC, abstractmethod
from typing import List, Dict, Any


class VectorStore(ABC):
    """Abstract base class for vector storage operations."""

    @abstractmethod
    def upsert(self, id: str, content: str, metadata: Dict[str, Any], embedding: List[float]) -> None:
        """Insert or update a single document."""

    @abstractmethod
    def batch_upsert(self, ids: List[str], contents: List[str],
                     metadatas: List[Dict[str, Any]], embeddings: List[List[float]]) -> None:
        """Insert or update multiple documents in a single call."""

    @abstractmethod
    def search(self, query_embedding: List[float], n_results: int = 5) -> List[Dict[str, Any]]:
        """Search for similar documents. Returns dicts with 'id', 'content', 'metadata', 'distance'."""

    @abstractmethod
    def delete(self, id: str) -> None:
        """Delete a document by its ID."""

    @abstractmethod
    def delete_by_metadata(self, filter: Dict[str, Any]) -> None:
        """Delete documents matching the metadata filter."""

    @abstractmethod
    def wipe(self) -> None:
        """Clear all data from the vector store."""
