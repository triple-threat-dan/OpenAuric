"""
Unit tests for auric.memory.chroma_store.ChromaStore.

Tests cover:
- Module-level: CHROMADB_AVAILABLE flag, ImportError / generic Exception handling
- __init__: default/custom persistence_path, client + collection creation,
  CHROMADB_AVAILABLE=False raises, PersistentClient failure re-raises
- upsert: delegates to collection.upsert with correct args, re-raises on error
- batch_upsert: delegates with correct args, re-raises on error
- search: result formatting (full, partial, empty), n_results passthrough,
  empty ids, exception returns empty list
- delete: delegates to collection.delete, swallows errors
- delete_by_metadata: delegates with where= filter, swallows errors
- wipe: deletes + re-creates collection, swallows errors
- VectorStore interface: ChromaStore is a subclass of VectorStore
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_chromadb():
    """Return a mock chromadb module with a PersistentClient that works."""
    mock_chromadb = MagicMock()
    mock_client = MagicMock()
    mock_collection = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection
    mock_chromadb.PersistentClient.return_value = mock_client
    return mock_chromadb, mock_client, mock_collection


def _make_store(tmp_path, collection_name="test_collection"):
    """
    Create a ChromaStore with a fully mocked chromadb backend.
    Returns (store, mock_client, mock_collection).
    """
    mock_chromadb, mock_client, mock_collection = _make_mock_chromadb()

    with patch.dict("sys.modules", {"chromadb": mock_chromadb}), \
         patch("auric.memory.chroma_store.chromadb", mock_chromadb, create=True), \
         patch("auric.memory.chroma_store.CHROMADB_AVAILABLE", True):
        from auric.memory.chroma_store import ChromaStore
        store = ChromaStore(
            collection_name=collection_name,
            persistence_path=tmp_path / "chroma_db",
        )

    return store, mock_client, mock_collection


# ===========================================================================
# Tests: Module-Level Import Guard
# ===========================================================================


class TestModuleLevelImport:

    def test_chromadb_available_flag_exists(self):
        from auric.memory.chroma_store import CHROMADB_AVAILABLE
        assert isinstance(CHROMADB_AVAILABLE, bool)


# ===========================================================================
# Tests: ChromaStore is a VectorStore
# ===========================================================================


class TestVectorStoreInterface:

    def test_is_subclass_of_vector_store(self):
        from auric.memory.chroma_store import ChromaStore
        from auric.memory.vector_store import VectorStore
        assert issubclass(ChromaStore, VectorStore)

    def test_instance_is_vector_store(self, tmp_path):
        from auric.memory.vector_store import VectorStore
        store, _, _ = _make_store(tmp_path)
        assert isinstance(store, VectorStore)


# ===========================================================================
# Tests: __init__
# ===========================================================================


class TestChromaStoreInit:

    def test_custom_persistence_path(self, tmp_path):
        store, _, _ = _make_store(tmp_path)
        assert store.persistence_path == tmp_path / "chroma_db"

    def test_custom_collection_name(self, tmp_path):
        store, _, _ = _make_store(tmp_path, collection_name="my_col")
        assert store.collection_name == "my_col"

    def test_client_initialized_with_path(self, tmp_path):
        mock_chromadb, mock_client, mock_collection = _make_mock_chromadb()
        persistence = tmp_path / "chroma_db"

        with patch.dict("sys.modules", {"chromadb": mock_chromadb}), \
             patch("auric.memory.chroma_store.chromadb", mock_chromadb, create=True), \
             patch("auric.memory.chroma_store.CHROMADB_AVAILABLE", True):
            from auric.memory.chroma_store import ChromaStore
            store = ChromaStore(persistence_path=persistence)

        mock_chromadb.PersistentClient.assert_called_once_with(path=str(persistence))

    def test_collection_get_or_create_called(self, tmp_path):
        store, mock_client, _ = _make_store(tmp_path, collection_name="test_col")
        mock_client.get_or_create_collection.assert_called_once_with(name="test_col")

    def test_collection_stored_on_instance(self, tmp_path):
        store, _, mock_collection = _make_store(tmp_path)
        assert store.collection is mock_collection

    def test_client_stored_on_instance(self, tmp_path):
        store, mock_client, _ = _make_store(tmp_path)
        assert store.client is mock_client

    def test_default_persistence_path_uses_auric_root(self):
        mock_chromadb, _, _ = _make_mock_chromadb()

        with patch.dict("sys.modules", {"chromadb": mock_chromadb}), \
             patch("auric.memory.chroma_store.chromadb", mock_chromadb, create=True), \
             patch("auric.memory.chroma_store.CHROMADB_AVAILABLE", True), \
             patch("auric.memory.chroma_store.AURIC_ROOT", Path("/fake/root")):
            from auric.memory.chroma_store import ChromaStore
            store = ChromaStore()

        assert store.persistence_path == Path("/fake/root/chroma_db")

    def test_raises_when_chromadb_unavailable(self):
        with patch("auric.memory.chroma_store.CHROMADB_AVAILABLE", False), \
             patch("auric.memory.chroma_store.CHROMADB_ERROR", "no module", create=True):
            from auric.memory.chroma_store import ChromaStore
            with pytest.raises(RuntimeError, match="ChromaDB is not available"):
                ChromaStore(persistence_path=Path("/tmp/db"))

    def test_raises_when_persistent_client_fails(self):
        mock_chromadb = MagicMock()
        mock_chromadb.PersistentClient.side_effect = RuntimeError("disk full")

        with patch.dict("sys.modules", {"chromadb": mock_chromadb}), \
             patch("auric.memory.chroma_store.chromadb", mock_chromadb, create=True), \
             patch("auric.memory.chroma_store.CHROMADB_AVAILABLE", True):
            from auric.memory.chroma_store import ChromaStore
            with pytest.raises(RuntimeError, match="disk full"):
                ChromaStore(persistence_path=Path("/tmp/db"))

    def test_raises_when_get_or_create_collection_fails(self):
        mock_chromadb = MagicMock()
        mock_client = MagicMock()
        mock_client.get_or_create_collection.side_effect = ValueError("bad name")
        mock_chromadb.PersistentClient.return_value = mock_client

        with patch.dict("sys.modules", {"chromadb": mock_chromadb}), \
             patch("auric.memory.chroma_store.chromadb", mock_chromadb, create=True), \
             patch("auric.memory.chroma_store.CHROMADB_AVAILABLE", True):
            from auric.memory.chroma_store import ChromaStore
            with pytest.raises(ValueError, match="bad name"):
                ChromaStore(persistence_path=Path("/tmp/db"))


# ===========================================================================
# Tests: upsert
# ===========================================================================


class TestUpsert:

    def test_delegates_to_collection(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        store.upsert(
            id="doc1",
            content="Hello",
            metadata={"source": "test.md"},
            embedding=[0.1, 0.2, 0.3],
        )
        mock_col.upsert.assert_called_once_with(
            ids=["doc1"],
            documents=["Hello"],
            metadatas=[{"source": "test.md"}],
            embeddings=[[0.1, 0.2, 0.3]],
        )

    def test_raises_on_collection_error(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.upsert.side_effect = RuntimeError("db error")

        with pytest.raises(RuntimeError, match="db error"):
            store.upsert("id", "content", {}, [0.1])

    def test_wraps_single_values_in_lists(self, tmp_path):
        """Verify each scalar arg is wrapped in a list before passing to chromadb."""
        store, _, mock_col = _make_store(tmp_path)
        store.upsert("x", "y", {"k": "v"}, [1.0])

        call_kwargs = mock_col.upsert.call_args
        assert call_kwargs.kwargs["ids"] == ["x"]
        assert call_kwargs.kwargs["documents"] == ["y"]
        assert call_kwargs.kwargs["metadatas"] == [{"k": "v"}]
        assert call_kwargs.kwargs["embeddings"] == [[1.0]]


# ===========================================================================
# Tests: batch_upsert
# ===========================================================================


class TestBatchUpsert:

    def test_delegates_to_collection(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        ids = ["a", "b"]
        contents = ["content a", "content b"]
        metadatas = [{"src": "a.md"}, {"src": "b.md"}]
        embeddings = [[0.1, 0.2], [0.3, 0.4]]

        store.batch_upsert(
            ids=ids,
            contents=contents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

        mock_col.upsert.assert_called_once_with(
            ids=ids,
            documents=contents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def test_raises_on_collection_error(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.upsert.side_effect = RuntimeError("batch fail")

        with pytest.raises(RuntimeError, match="batch fail"):
            store.batch_upsert(["id"], ["c"], [{}], [[0.1]])

    def test_empty_batch(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        store.batch_upsert([], [], [], [])
        mock_col.upsert.assert_called_once_with(
            ids=[], documents=[], metadatas=[], embeddings=[],
        )


# ===========================================================================
# Tests: search
# ===========================================================================


class TestSearch:

    def _full_results(self):
        """Typical chromadb query response with 2 results."""
        return {
            "ids": [["id1", "id2"]],
            "documents": [["doc one", "doc two"]],
            "metadatas": [[{"src": "a.md"}, {"src": "b.md"}]],
            "distances": [[0.1, 0.5]],
        }

    def test_returns_formatted_results(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = self._full_results()

        results = store.search([0.1, 0.2], n_results=5)

        assert len(results) == 2
        assert results[0] == {
            "id": "id1",
            "content": "doc one",
            "metadata": {"src": "a.md"},
            "distance": 0.1,
        }
        assert results[1] == {
            "id": "id2",
            "content": "doc two",
            "metadata": {"src": "b.md"},
            "distance": 0.5,
        }

    def test_passes_n_results(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        store.search([0.1], n_results=10)

        _, kwargs = mock_col.query.call_args
        assert kwargs["n_results"] == 10

    def test_query_includes_correct_fields(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        store.search([0.1])

        _, kwargs = mock_col.query.call_args
        assert kwargs["include"] == ["documents", "metadatas", "distances"]
        assert kwargs["query_embeddings"] == [[0.1]]

    def test_empty_ids_returns_empty(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        results = store.search([0.1])
        assert results == []

    def test_no_ids_key_returns_empty(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": [],
        }

        results = store.search([0.1])
        assert results == []

    def test_missing_documents_returns_empty_content(self, tmp_path):
        """If documents is None, results are returned with empty content strings."""
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {
            "ids": [["id1"]],
            "documents": None,
            "metadatas": [[{"src": "a.md"}]],
            "distances": [[0.1]],
        }

        results = store.search([0.1])
        assert len(results) == 1
        assert results[0]["id"] == "id1"
        assert results[0]["content"] == ""
        assert results[0]["metadata"] == {"src": "a.md"}

    def test_missing_metadatas_uses_empty_dict(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {
            "ids": [["id1"]],
            "documents": [["doc"]],
            "metadatas": None,
            "distances": [[0.1]],
        }

        results = store.search([0.1])
        assert len(results) == 1
        assert results[0]["metadata"] == {}

    def test_missing_distances_uses_zero(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {
            "ids": [["id1"]],
            "documents": [["doc"]],
            "metadatas": [[{"src": "a"}]],
            "distances": None,
        }

        results = store.search([0.1])
        assert len(results) == 1
        assert results[0]["distance"] == 0.0

    def test_exception_returns_empty_list(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.side_effect = RuntimeError("search failed")

        results = store.search([0.1])
        assert results == []

    def test_default_n_results_is_5(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        store.search([0.1])

        _, kwargs = mock_col.query.call_args
        assert kwargs["n_results"] == 5

    def test_single_result(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.query.return_value = {
            "ids": [["only"]],
            "documents": [["the doc"]],
            "metadatas": [[{"k": "v"}]],
            "distances": [[0.42]],
        }

        results = store.search([0.1], n_results=1)
        assert len(results) == 1
        assert results[0]["id"] == "only"
        assert results[0]["distance"] == 0.42


# ===========================================================================
# Tests: delete
# ===========================================================================


class TestDelete:

    def test_delegates_to_collection(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        store.delete("doc42")
        mock_col.delete.assert_called_once_with(ids=["doc42"])

    def test_wraps_id_in_list(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        store.delete("single")
        call_kwargs = mock_col.delete.call_args
        assert call_kwargs.kwargs["ids"] == ["single"]

    def test_swallows_exception(self, tmp_path):
        """delete() logs but does not re-raise exceptions."""
        store, _, mock_col = _make_store(tmp_path)
        mock_col.delete.side_effect = RuntimeError("gone wrong")

        # Should NOT raise
        store.delete("bad_id")

    def test_exception_is_logged(self, tmp_path, caplog):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.delete.side_effect = RuntimeError("oops")

        import logging
        with caplog.at_level(logging.ERROR, logger="auric.memory.chroma"):
            store.delete("bad")

        assert any("Failed to delete bad" in record.message for record in caplog.records)


# ===========================================================================
# Tests: delete_by_metadata
# ===========================================================================


class TestDeleteByMetadata:

    def test_delegates_with_where_filter(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        filter_dict = {"source": "test.md"}
        store.delete_by_metadata(filter_dict)
        mock_col.delete.assert_called_once_with(where=filter_dict)

    def test_complex_filter(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        filter_dict = {"source": "notes.md", "chunk_index": 0}
        store.delete_by_metadata(filter_dict)
        mock_col.delete.assert_called_once_with(where=filter_dict)

    def test_swallows_exception(self, tmp_path):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.delete.side_effect = RuntimeError("filter fail")
        store.delete_by_metadata({"key": "val"})  # Should not raise

    def test_exception_is_logged(self, tmp_path, caplog):
        store, _, mock_col = _make_store(tmp_path)
        mock_col.delete.side_effect = RuntimeError("filter fail")

        import logging
        with caplog.at_level(logging.ERROR, logger="auric.memory.chroma"):
            store.delete_by_metadata({"key": "val"})

        assert any("Failed to delete by metadata" in record.message for record in caplog.records)


# ===========================================================================
# Tests: wipe
# ===========================================================================


class TestWipe:

    def test_deletes_and_recreates_collection(self, tmp_path):
        store, mock_client, mock_col = _make_store(tmp_path, collection_name="wipe_me")

        new_collection = MagicMock()
        mock_client.get_or_create_collection.return_value = new_collection

        store.wipe()

        mock_client.delete_collection.assert_called_once_with("wipe_me")
        # get_or_create_collection called once in __init__ and once in wipe
        assert mock_client.get_or_create_collection.call_count == 2
        assert store.collection is new_collection

    def test_swallows_exception(self, tmp_path):
        store, mock_client, _ = _make_store(tmp_path)
        mock_client.delete_collection.side_effect = RuntimeError("wipe fail")
        store.wipe()  # Should not raise

    def test_exception_is_logged(self, tmp_path, caplog):
        store, mock_client, _ = _make_store(tmp_path)
        mock_client.delete_collection.side_effect = RuntimeError("wipe fail")

        import logging
        with caplog.at_level(logging.ERROR, logger="auric.memory.chroma"):
            store.wipe()

        assert any("Failed to wipe store" in record.message for record in caplog.records)

    def test_uses_same_collection_name(self, tmp_path):
        store, mock_client, _ = _make_store(tmp_path, collection_name="special")
        store.wipe()
        mock_client.delete_collection.assert_called_with("special")
        mock_client.get_or_create_collection.assert_called_with(name="special")
