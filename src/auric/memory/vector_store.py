from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class VectorStore(ABC):
    """
    Abstract base class for vector storage operations.
    Allows the application to be agnostic to the underlying vector DB implementation.
    """

    @abstractmethod
    def upsert(self, id: str, content: str, metadata: Dict[str, Any], embedding: List[float]) -> None:
        """
        Insert or update a document in the vector store.
        """
        pass

    @abstractmethod
    def batch_upsert(self, ids: List[str], contents: List[str], metadatas: List[Dict[str, Any]], embeddings: List[List[float]]) -> None:
        """
        Insert or update multiple documents in a single call.
        """
        pass

    @abstractmethod
    def search(self, query_embedding: List[float], n_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search for similar documents using a query embedding.
        Returns a list of results, where each result is a dictionary containing
        'id', 'content', 'metadata', and 'distance'.
        """
        pass

    @abstractmethod
    def delete(self, id: str) -> None:
        """
        Delete a document by its ID.
        """
        pass

    @abstractmethod
    def delete_by_metadata(self, filter: Dict[str, Any]) -> None:
        """
        Delete documents matching the metadata filter.
        """
        pass

    @abstractmethod
    def wipe(self) -> None:
        """
        Clear all data from the vector store.
        """
        pass
