"""
Unit tests for auric.memory.vector_store.VectorStore.

Tests cover:
- ABC contract: cannot instantiate directly, all 6 methods are abstract
- Partial implementation: missing any single method prevents instantiation
- Complete implementation: a conforming subclass can be instantiated and called
- Method signatures: correct parameter names, default values, return types
- isinstance/issubclass checks with concrete implementations
- ChromaStore is a valid VectorStore subclass (integration check)
"""

import pytest
from abc import ABC
from typing import List, Dict, Any

from auric.memory.vector_store import VectorStore


# ===========================================================================
# Tests: ABC Identity
# ===========================================================================


class TestVectorStoreABC:

    def test_is_abstract_base_class(self):
        assert issubclass(VectorStore, ABC)

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError, match="abstract method"):
            VectorStore()

    def test_has_exactly_six_abstract_methods(self):
        expected = {"upsert", "batch_upsert", "search", "delete", "delete_by_metadata", "wipe"}
        assert VectorStore.__abstractmethods__ == expected


# ===========================================================================
# Tests: Partial Implementation Raises
# ===========================================================================


class TestPartialImplementation:
    """Omitting any single abstract method should prevent instantiation."""

    def _make_partial(self, *, exclude: str):
        """Build a subclass that implements all methods except `exclude`."""
        methods = {
            "upsert": lambda self, id, content, metadata, embedding: None,
            "batch_upsert": lambda self, ids, contents, metadatas, embeddings: None,
            "search": lambda self, query_embedding, n_results=5: [],
            "delete": lambda self, id: None,
            "delete_by_metadata": lambda self, filter: None,
            "wipe": lambda self: None,
        }
        methods.pop(exclude)
        return type("PartialStore", (VectorStore,), methods)

    def test_missing_upsert(self):
        cls = self._make_partial(exclude="upsert")
        with pytest.raises(TypeError):
            cls()

    def test_missing_batch_upsert(self):
        cls = self._make_partial(exclude="batch_upsert")
        with pytest.raises(TypeError):
            cls()

    def test_missing_search(self):
        cls = self._make_partial(exclude="search")
        with pytest.raises(TypeError):
            cls()

    def test_missing_delete(self):
        cls = self._make_partial(exclude="delete")
        with pytest.raises(TypeError):
            cls()

    def test_missing_delete_by_metadata(self):
        cls = self._make_partial(exclude="delete_by_metadata")
        with pytest.raises(TypeError):
            cls()

    def test_missing_wipe(self):
        cls = self._make_partial(exclude="wipe")
        with pytest.raises(TypeError):
            cls()


# ===========================================================================
# Tests: Complete Implementation
# ===========================================================================


class _ConcreteStore(VectorStore):
    """Minimal concrete implementation for testing."""

    def __init__(self):
        self.calls = []

    def upsert(self, id: str, content: str, metadata: Dict[str, Any], embedding: List[float]) -> None:
        self.calls.append(("upsert", id, content, metadata, embedding))

    def batch_upsert(self, ids: List[str], contents: List[str],
                     metadatas: List[Dict[str, Any]], embeddings: List[List[float]]) -> None:
        self.calls.append(("batch_upsert", ids, contents, metadatas, embeddings))

    def search(self, query_embedding: List[float], n_results: int = 5) -> List[Dict[str, Any]]:
        self.calls.append(("search", query_embedding, n_results))
        return [{"id": "mock", "content": "result", "metadata": {}, "distance": 0.0}]

    def delete(self, id: str) -> None:
        self.calls.append(("delete", id))

    def delete_by_metadata(self, filter: Dict[str, Any]) -> None:
        self.calls.append(("delete_by_metadata", filter))

    def wipe(self) -> None:
        self.calls.append(("wipe",))


class TestConcreteImplementation:

    def test_can_instantiate(self):
        store = _ConcreteStore()
        assert isinstance(store, VectorStore)

    def test_isinstance_check(self):
        assert issubclass(_ConcreteStore, VectorStore)

    def test_upsert_callable(self):
        store = _ConcreteStore()
        store.upsert("id1", "content", {"k": "v"}, [0.1, 0.2])
        assert store.calls[-1] == ("upsert", "id1", "content", {"k": "v"}, [0.1, 0.2])

    def test_batch_upsert_callable(self):
        store = _ConcreteStore()
        store.batch_upsert(["a", "b"], ["c1", "c2"], [{}, {}], [[0.1], [0.2]])
        assert store.calls[-1][0] == "batch_upsert"
        assert store.calls[-1][1] == ["a", "b"]

    def test_search_callable_with_default_n(self):
        store = _ConcreteStore()
        results = store.search([0.1, 0.2])
        assert store.calls[-1] == ("search", [0.1, 0.2], 5)
        assert isinstance(results, list)

    def test_search_callable_with_custom_n(self):
        store = _ConcreteStore()
        store.search([0.1], n_results=10)
        assert store.calls[-1] == ("search", [0.1], 10)

    def test_delete_callable(self):
        store = _ConcreteStore()
        store.delete("doc1")
        assert store.calls[-1] == ("delete", "doc1")

    def test_delete_by_metadata_callable(self):
        store = _ConcreteStore()
        store.delete_by_metadata({"source": "test.md"})
        assert store.calls[-1] == ("delete_by_metadata", {"source": "test.md"})

    def test_wipe_callable(self):
        store = _ConcreteStore()
        store.wipe()
        assert store.calls[-1] == ("wipe",)

    def test_multiple_calls_tracked(self):
        store = _ConcreteStore()
        store.upsert("a", "b", {}, [0.1])
        store.delete("a")
        store.wipe()
        assert len(store.calls) == 3


# ===========================================================================
# Tests: Method Signatures
# ===========================================================================


class TestMethodSignatures:
    """Verify the ABC defines the expected parameter names and defaults."""

    def test_upsert_params(self):
        import inspect
        sig = inspect.signature(VectorStore.upsert)
        params = list(sig.parameters.keys())
        assert params == ["self", "id", "content", "metadata", "embedding"]

    def test_batch_upsert_params(self):
        import inspect
        sig = inspect.signature(VectorStore.batch_upsert)
        params = list(sig.parameters.keys())
        assert params == ["self", "ids", "contents", "metadatas", "embeddings"]

    def test_search_params_and_default(self):
        import inspect
        sig = inspect.signature(VectorStore.search)
        params = list(sig.parameters.keys())
        assert params == ["self", "query_embedding", "n_results"]
        assert sig.parameters["n_results"].default == 5

    def test_delete_params(self):
        import inspect
        sig = inspect.signature(VectorStore.delete)
        params = list(sig.parameters.keys())
        assert params == ["self", "id"]

    def test_delete_by_metadata_params(self):
        import inspect
        sig = inspect.signature(VectorStore.delete_by_metadata)
        params = list(sig.parameters.keys())
        assert params == ["self", "filter"]

    def test_wipe_params(self):
        import inspect
        sig = inspect.signature(VectorStore.wipe)
        params = list(sig.parameters.keys())
        assert params == ["self"]


# ===========================================================================
# Tests: ChromaStore Integration Check
# ===========================================================================


class TestChromaStoreIsVectorStore:
    """Verify that the project's concrete ChromaStore is a valid subclass."""

    def test_chromastore_is_subclass(self):
        from auric.memory.chroma_store import ChromaStore
        assert issubclass(ChromaStore, VectorStore)
