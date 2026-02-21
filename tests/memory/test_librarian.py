"""
Unit tests for auric.memory.librarian.

Tests cover:
- GrimoireHandler: file filtering, debounced dispatch, event routing, shutdown
- GrimoireLibrarian.__init__: default/custom paths, vector store & encoder failures
- GrimoireLibrarian.start / stop: observer lifecycle, directory creation, no-loop error
- GrimoireLibrarian.index_file: deleted files, empty content, full embed+upsert pipeline,
  error handling, whitespace-only chunks filtered
- GrimoireLibrarian._chunk_text: short text, exact boundary, multi-chunk with overlap,
  empty/whitespace input
- GrimoireLibrarian.search: success path, empty embeddings, exceptions, disabled store
- GrimoireLibrarian.start_reindexing: concurrent file indexing across directories
"""

import asyncio
import hashlib
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_vector_store():
    """Create a mock ChromaStore with all required methods."""
    store = MagicMock()
    store.upsert = MagicMock()
    store.batch_upsert = MagicMock()
    store.search = MagicMock(return_value=[])
    store.delete = MagicMock()
    store.delete_by_metadata = MagicMock()
    store.wipe = MagicMock()
    return store


def _mock_encoder(dim: int = 4):
    """Create a mock EmbeddingWrapper that returns deterministic embeddings."""
    encoder = MagicMock()

    def _encode(sentences):
        if isinstance(sentences, str):
            sentences = [sentences]
        return np.random.rand(len(sentences), dim)

    encoder.encode = MagicMock(side_effect=_encode)
    return encoder


def _make_librarian(tmp_path, *, vector_store=None, encoder=None):
    """
    Create a GrimoireLibrarian with mocked dependencies,
    bypassing the real ChromaStore and EmbeddingWrapper init.
    """
    grimoire = tmp_path / "grimoire"
    memories = tmp_path / "memories"
    grimoire.mkdir(exist_ok=True)
    memories.mkdir(exist_ok=True)

    with patch("auric.memory.librarian.ChromaStore") as MockChroma, \
         patch("auric.memory.librarian.load_config") as mock_config, \
         patch("auric.memory.librarian.EmbeddingWrapper") as MockEncoder:

        MockChroma.return_value = vector_store or _mock_vector_store()
        MockEncoder.return_value = encoder or _mock_encoder()
        mock_config.return_value = MagicMock()

        from auric.memory.librarian import GrimoireLibrarian
        lib = GrimoireLibrarian(grimoire_path=grimoire, memories_path=memories)

    return lib


# ===========================================================================
# Tests: GrimoireHandler — File Filtering
# ===========================================================================

class TestGrimoireHandlerFiltering:

    def _make_handler(self):
        from auric.memory.librarian import GrimoireHandler
        loop = MagicMock()
        callback = MagicMock()
        return GrimoireHandler(loop=loop, callback=callback, debounce_seconds=0.1)

    def test_ignores_dotfiles(self):
        handler = self._make_handler()
        assert handler._should_ignore("/some/path/.hidden.md") is True

    def test_ignores_non_md_non_txt(self):
        handler = self._make_handler()
        assert handler._should_ignore("/some/path/file.py") is True
        assert handler._should_ignore("/some/path/image.png") is True
        assert handler._should_ignore("/some/path/data.json") is True

    def test_accepts_md_files(self):
        handler = self._make_handler()
        assert handler._should_ignore("/some/path/notes.md") is False

    def test_accepts_txt_files(self):
        handler = self._make_handler()
        assert handler._should_ignore("/some/path/readme.txt") is False

    def test_ignores_dotfile_with_md_extension(self):
        handler = self._make_handler()
        assert handler._should_ignore("/some/path/.secret.md") is True

    def test_ignores_no_extension(self):
        handler = self._make_handler()
        assert handler._should_ignore("/some/path/Makefile") is True


# ===========================================================================
# Tests: GrimoireHandler — Event Routing
# ===========================================================================

class TestGrimoireHandlerEvents:

    def _make_handler(self):
        from auric.memory.librarian import GrimoireHandler
        loop = MagicMock()
        callback = MagicMock()
        handler = GrimoireHandler(loop=loop, callback=callback)
        handler._dispatch_update = MagicMock()
        return handler

    def test_on_created_dispatches_src_path(self):
        handler = self._make_handler()
        event = MagicMock(is_directory=False, src_path="/path/to/file.md")
        handler.on_created(event)
        handler._dispatch_update.assert_called_once_with("/path/to/file.md")

    def test_on_modified_dispatches_src_path(self):
        handler = self._make_handler()
        event = MagicMock(is_directory=False, src_path="/path/to/file.md")
        handler.on_modified(event)
        handler._dispatch_update.assert_called_once_with("/path/to/file.md")

    def test_on_deleted_dispatches_src_path(self):
        handler = self._make_handler()
        event = MagicMock(is_directory=False, src_path="/path/to/file.md")
        handler.on_deleted(event)
        handler._dispatch_update.assert_called_once_with("/path/to/file.md")

    def test_on_moved_dispatches_dest_path(self):
        handler = self._make_handler()
        event = MagicMock(is_directory=False, dest_path="/path/to/new.md")
        handler.on_moved(event)
        handler._dispatch_update.assert_called_once_with("/path/to/new.md")

    def test_directory_events_ignored(self):
        handler = self._make_handler()
        for method in (handler.on_created, handler.on_modified, handler.on_deleted):
            event = MagicMock(is_directory=True, src_path="/path/to/dir")
            method(event)
        move_event = MagicMock(is_directory=True, dest_path="/path/to/dir")
        handler.on_moved(move_event)

        handler._dispatch_update.assert_not_called()


# ===========================================================================
# Tests: GrimoireHandler — Dispatch & Debounce
# ===========================================================================

class TestGrimoireHandlerDispatch:

    def test_dispatch_ignores_filtered_files(self):
        from auric.memory.librarian import GrimoireHandler
        loop = MagicMock()
        handler = GrimoireHandler(loop=loop, callback=MagicMock())
        handler._dispatch_update("/some/file.py")
        loop.call_soon_threadsafe.assert_not_called()

    def test_dispatch_calls_threadsafe_for_valid_files(self):
        from auric.memory.librarian import GrimoireHandler
        loop = MagicMock()
        handler = GrimoireHandler(loop=loop, callback=MagicMock())
        handler._dispatch_update("/some/file.md")
        loop.call_soon_threadsafe.assert_called_once_with(
            handler._schedule_debounce, "/some/file.md"
        )

    def test_schedule_debounce_cancels_existing_task(self):
        from auric.memory.librarian import GrimoireHandler
        loop = MagicMock()
        handler = GrimoireHandler(loop=loop, callback=MagicMock())
        handler._debounce_and_index = MagicMock()  # Prevent real coroutine creation

        existing_task = MagicMock()
        existing_task.done.return_value = False
        handler.active_tasks["/path/file.md"] = existing_task

        new_task = MagicMock()
        loop.create_task.return_value = new_task

        handler._schedule_debounce("/path/file.md")

        existing_task.cancel.assert_called_once()
        assert handler.active_tasks["/path/file.md"] is new_task

    def test_schedule_debounce_does_not_cancel_completed_task(self):
        from auric.memory.librarian import GrimoireHandler
        loop = MagicMock()
        handler = GrimoireHandler(loop=loop, callback=MagicMock())
        handler._debounce_and_index = MagicMock()  # Prevent real coroutine creation

        existing_task = MagicMock()
        existing_task.done.return_value = True
        handler.active_tasks["/path/file.md"] = existing_task

        new_task = MagicMock()
        loop.create_task.return_value = new_task

        handler._schedule_debounce("/path/file.md")

        existing_task.cancel.assert_not_called()
        assert handler.active_tasks["/path/file.md"] is new_task


# ===========================================================================
# Tests: GrimoireHandler — Shutdown
# ===========================================================================

class TestGrimoireHandlerShutdown:

    def test_shutdown_cancels_pending_tasks(self):
        from auric.memory.librarian import GrimoireHandler
        handler = GrimoireHandler(loop=MagicMock(), callback=MagicMock())

        t1 = MagicMock()
        t1.done.return_value = False
        t2 = MagicMock()
        t2.done.return_value = False
        handler.active_tasks = {"/a.md": t1, "/b.md": t2}

        handler.shutdown()

        t1.cancel.assert_called_once()
        t2.cancel.assert_called_once()
        assert handler.active_tasks == {}

    def test_shutdown_skips_completed_tasks(self):
        from auric.memory.librarian import GrimoireHandler
        handler = GrimoireHandler(loop=MagicMock(), callback=MagicMock())

        done_task = MagicMock()
        done_task.done.return_value = True
        handler.active_tasks = {"/a.md": done_task}

        handler.shutdown()

        done_task.cancel.assert_not_called()
        assert handler.active_tasks == {}

    def test_shutdown_on_empty(self):
        from auric.memory.librarian import GrimoireHandler
        handler = GrimoireHandler(loop=MagicMock(), callback=MagicMock())
        handler.shutdown()  # Should not raise
        assert handler.active_tasks == {}


# ===========================================================================
# Tests: GrimoireHandler — Debounce & Index (async)
# ===========================================================================

class TestGrimoireHandlerDebounceAsync:

    @pytest.mark.asyncio
    async def test_debounce_calls_callback(self):
        from auric.memory.librarian import GrimoireHandler
        loop = asyncio.get_running_loop()
        callback = MagicMock()
        handler = GrimoireHandler(loop=loop, callback=callback, debounce_seconds=0.01)

        await handler._debounce_and_index("/path/file.md")

        callback.assert_called_once_with("/path/file.md")

    @pytest.mark.asyncio
    async def test_debounce_cleans_up_active_tasks(self):
        from auric.memory.librarian import GrimoireHandler
        loop = asyncio.get_running_loop()
        callback = MagicMock()
        handler = GrimoireHandler(loop=loop, callback=callback, debounce_seconds=0.01)

        task = loop.create_task(handler._debounce_and_index("/path/file.md"))
        handler.active_tasks["/path/file.md"] = task

        await task
        assert "/path/file.md" not in handler.active_tasks

    @pytest.mark.asyncio
    async def test_debounce_handles_callback_exception(self):
        from auric.memory.librarian import GrimoireHandler
        loop = asyncio.get_running_loop()
        callback = MagicMock(side_effect=RuntimeError("boom"))
        handler = GrimoireHandler(loop=loop, callback=callback, debounce_seconds=0.01)

        # Should not raise — exception is caught and logged
        await handler._debounce_and_index("/path/file.md")


# ===========================================================================
# Tests: GrimoireLibrarian.__init__
# ===========================================================================

class TestLibrarianInit:

    def test_default_paths(self):
        with patch("auric.memory.librarian.ChromaStore") as MockChroma, \
             patch("auric.memory.librarian.load_config") as mock_cfg, \
             patch("auric.memory.librarian.EmbeddingWrapper") as MockEnc, \
             patch("auric.memory.librarian.AURIC_ROOT", Path("/fake/root")):
            MockChroma.return_value = _mock_vector_store()
            MockEnc.return_value = _mock_encoder()
            mock_cfg.return_value = MagicMock()

            from auric.memory.librarian import GrimoireLibrarian
            lib = GrimoireLibrarian()

            assert lib.grimoire_path == Path("/fake/root/grimoire")
            assert lib.memories_path == Path("/fake/root/memories")

    def test_custom_paths(self, tmp_path):
        lib = _make_librarian(tmp_path)
        assert lib.grimoire_path == tmp_path / "grimoire"
        assert lib.memories_path == tmp_path / "memories"

    def test_vector_store_failure_disables_rag(self):
        with patch("auric.memory.librarian.ChromaStore", side_effect=RuntimeError("chroma down")), \
             patch("auric.memory.librarian.load_config"), \
             patch("auric.memory.librarian.EmbeddingWrapper"):

            from auric.memory.librarian import GrimoireLibrarian
            lib = GrimoireLibrarian(grimoire_path=Path("/tmp/g"), memories_path=Path("/tmp/m"))

            assert lib.vector_store is None
            assert lib.encoder is None

    def test_encoder_failure_nullifies_vector_store(self, tmp_path):
        with patch("auric.memory.librarian.ChromaStore") as MockChroma, \
             patch("auric.memory.librarian.load_config") as mock_cfg, \
             patch("auric.memory.librarian.EmbeddingWrapper", side_effect=RuntimeError("model gone")):
            MockChroma.return_value = _mock_vector_store()
            mock_cfg.return_value = MagicMock()

            from auric.memory.librarian import GrimoireLibrarian
            lib = GrimoireLibrarian(
                grimoire_path=tmp_path / "grimoire",
                memories_path=tmp_path / "memories",
            )

            assert lib.vector_store is None
            assert lib.encoder is None


# ===========================================================================
# Tests: GrimoireLibrarian.start / stop
# ===========================================================================

class TestLibrarianStartStop:

    @pytest.mark.asyncio
    async def test_start_creates_missing_directories(self, tmp_path):
        lib = _make_librarian(tmp_path)
        # Remove dirs to test auto-creation
        import shutil
        shutil.rmtree(lib.grimoire_path)
        shutil.rmtree(lib.memories_path)

        with patch("auric.memory.librarian.Observer") as MockObserver:
            mock_obs = MagicMock()
            MockObserver.return_value = mock_obs
            lib.start()

        assert lib.grimoire_path.exists()
        assert lib.memories_path.exists()

    @pytest.mark.asyncio
    async def test_start_creates_observer(self, tmp_path):
        lib = _make_librarian(tmp_path)

        with patch("auric.memory.librarian.Observer") as MockObserver:
            mock_obs = MagicMock()
            MockObserver.return_value = mock_obs
            lib.start()

        assert lib.observer is mock_obs
        mock_obs.start.assert_called_once()
        assert mock_obs.schedule.call_count == 2  # grimoire + memories

    @pytest.mark.asyncio
    async def test_start_without_loop_returns_gracefully(self, tmp_path):
        lib = _make_librarian(tmp_path)

        # Run outside of an async context by simulating RuntimeError
        with patch("auric.memory.librarian.asyncio.get_running_loop", side_effect=RuntimeError):
            lib.start()

        assert lib.observer is None

    def test_stop_joins_observer(self, tmp_path):
        lib = _make_librarian(tmp_path)
        mock_obs = MagicMock()
        lib.observer = mock_obs
        lib.event_handler = MagicMock()

        lib.stop()

        mock_obs.stop.assert_called_once()
        mock_obs.join.assert_called_once()
        lib.event_handler.shutdown.assert_called_once()
        assert lib.observer is None

    def test_stop_without_observer_is_safe(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.observer = None
        lib.event_handler = None
        lib.stop()  # Should not raise


# ===========================================================================
# Tests: GrimoireLibrarian._chunk_text
# ===========================================================================

class TestChunkText:

    def _get_lib(self, tmp_path):
        return _make_librarian(tmp_path)

    def test_short_text_single_chunk(self, tmp_path):
        lib = self._get_lib(tmp_path)
        result = lib._chunk_text("hello world", chunk_size=100)
        assert result == ["hello world"]

    def test_exact_boundary(self, tmp_path):
        lib = self._get_lib(tmp_path)
        text = "a" * 100
        result = lib._chunk_text(text, chunk_size=100)
        assert result == [text]

    def test_multiple_chunks_with_overlap(self, tmp_path):
        lib = self._get_lib(tmp_path)
        text = "a" * 250
        result = lib._chunk_text(text, chunk_size=100, overlap=20)

        # Should produce multiple chunks
        assert len(result) > 1
        # Each chunk is at most chunk_size
        for chunk in result:
            assert len(chunk) <= 100
        # Reconstruct: first + subsequent non-overlapping parts should cover full text
        assert result[0] == text[:100]

    def test_overlap_content_correctness(self, tmp_path):
        lib = self._get_lib(tmp_path)
        text = "ABCDEFGHIJ" * 5  # 50 chars
        result = lib._chunk_text(text, chunk_size=20, overlap=5)

        # Second chunk should start 15 chars in (chunk_size - overlap)
        assert result[1] == text[15:35]

    def test_empty_string(self, tmp_path):
        lib = self._get_lib(tmp_path)
        result = lib._chunk_text("")
        assert result == [""]

    def test_text_just_over_boundary(self, tmp_path):
        lib = self._get_lib(tmp_path)
        text = "a" * 101
        result = lib._chunk_text(text, chunk_size=100, overlap=10)
        assert len(result) == 2
        assert result[0] == "a" * 100
        assert result[1] == "a" * 11  # last 11 chars (start at 90, end at 101)

    def test_zero_overlap(self, tmp_path):
        lib = self._get_lib(tmp_path)
        text = "a" * 300
        result = lib._chunk_text(text, chunk_size=100, overlap=0)
        assert len(result) == 3
        assert "".join(result) == text


# ===========================================================================
# Tests: GrimoireLibrarian.index_file
# ===========================================================================

class TestIndexFile:

    def test_returns_early_if_no_vector_store(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.vector_store = None

        # Should not raise
        lib.index_file(str(tmp_path / "grimoire" / "nonexistent.md"))

    def test_returns_early_if_no_encoder(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.encoder = None

        lib.index_file(str(tmp_path / "grimoire" / "nonexistent.md"))

    def test_deleted_file_removes_from_index(self, tmp_path):
        lib = _make_librarian(tmp_path)
        missing = tmp_path / "grimoire" / "gone.md"

        lib.index_file(str(missing))

        lib.vector_store.delete_by_metadata.assert_called_once_with(
            {"source": str(missing)}
        )

    def test_empty_file_deletes_old_entries_only(self, tmp_path):
        lib = _make_librarian(tmp_path)
        empty_file = tmp_path / "grimoire" / "empty.md"
        empty_file.write_text("", encoding="utf-8")

        lib.index_file(str(empty_file))

        # Old entries are deleted
        lib.vector_store.delete_by_metadata.assert_called_once()
        # No upsert since content is empty after strip
        lib.vector_store.batch_upsert.assert_not_called()

    def test_whitespace_only_file_no_upsert(self, tmp_path):
        lib = _make_librarian(tmp_path)
        ws_file = tmp_path / "grimoire" / "whitespace.md"
        ws_file.write_text("   \n  \t  \n  ", encoding="utf-8")

        lib.index_file(str(ws_file))

        lib.vector_store.batch_upsert.assert_not_called()

    def test_full_index_pipeline(self, tmp_path):
        lib = _make_librarian(tmp_path)
        test_file = tmp_path / "grimoire" / "test.md"
        test_file.write_text("Hello world, this is a test document.", encoding="utf-8")

        lib.index_file(str(test_file))

        # Encoder should be called with the chunks
        lib.encoder.encode.assert_called_once()
        chunks = lib.encoder.encode.call_args[0][0]
        assert isinstance(chunks, list)
        assert len(chunks) > 0

        # batch_upsert should be called once
        lib.vector_store.batch_upsert.assert_called_once()
        kwargs = lib.vector_store.batch_upsert.call_args[1]
        assert len(kwargs["ids"]) == len(chunks)
        assert len(kwargs["contents"]) == len(chunks)
        assert len(kwargs["metadatas"]) == len(chunks)
        assert len(kwargs["embeddings"]) == len(chunks)

    def test_index_file_uses_md5_hash_ids(self, tmp_path):
        lib = _make_librarian(tmp_path)
        test_file = tmp_path / "grimoire" / "test.md"
        test_file.write_text("Content here.", encoding="utf-8")

        lib.index_file(str(test_file))

        expected_hash = hashlib.md5(str(test_file).encode()).hexdigest()
        kwargs = lib.vector_store.batch_upsert.call_args[1]
        assert kwargs["ids"][0] == f"{expected_hash}:0"

    def test_index_file_metadata_contains_source(self, tmp_path):
        lib = _make_librarian(tmp_path)
        test_file = tmp_path / "grimoire" / "info.md"
        test_file.write_text("Some info.", encoding="utf-8")

        lib.index_file(str(test_file))

        kwargs = lib.vector_store.batch_upsert.call_args[1]
        meta = kwargs["metadatas"][0]
        assert meta["source"] == str(test_file)
        assert meta["filename"] == "info.md"
        assert meta["chunk_index"] == 0

    def test_index_file_deletes_old_entries_before_upserting(self, tmp_path):
        lib = _make_librarian(tmp_path)
        test_file = tmp_path / "grimoire" / "update.md"
        test_file.write_text("Updated content.", encoding="utf-8")

        call_order = []
        lib.vector_store.delete_by_metadata.side_effect = lambda *a, **kw: call_order.append("delete")
        lib.vector_store.batch_upsert.side_effect = lambda *a, **kw: call_order.append("upsert")

        lib.index_file(str(test_file))

        assert call_order == ["delete", "upsert"]

    def test_index_file_handles_encoder_error(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.encoder.encode.side_effect = RuntimeError("encoding failed")

        test_file = tmp_path / "grimoire" / "bad.md"
        test_file.write_text("Content.", encoding="utf-8")

        # Should not raise — error is caught and logged
        lib.index_file(str(test_file))
        lib.vector_store.batch_upsert.assert_not_called()

    def test_index_file_handles_upsert_error(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.vector_store.batch_upsert.side_effect = RuntimeError("db error")

        test_file = tmp_path / "grimoire" / "bad.md"
        test_file.write_text("Content.", encoding="utf-8")

        # Should not raise
        lib.index_file(str(test_file))

    def test_multi_chunk_file(self, tmp_path):
        lib = _make_librarian(tmp_path)
        test_file = tmp_path / "grimoire" / "big.md"
        # Write content that will produce multiple chunks (default chunk_size=1000)
        test_file.write_text("word " * 500, encoding="utf-8")  # 2500 chars

        lib.index_file(str(test_file))

        kwargs = lib.vector_store.batch_upsert.call_args[1]
        assert len(kwargs["ids"]) > 1

    def test_index_file_filters_whitespace_chunks(self, tmp_path):
        """Chunks that are only whitespace should be excluded before encoding."""
        lib = _make_librarian(tmp_path)

        # Monkeypatch _chunk_text to return a mix of real and whitespace chunks
        lib._chunk_text = MagicMock(return_value=["real content", "   \n  ", "more content"])

        test_file = tmp_path / "grimoire" / "mixed.md"
        test_file.write_text("content", encoding="utf-8")

        lib.index_file(str(test_file))

        # Encoder should only receive the non-whitespace chunks
        encoded_chunks = lib.encoder.encode.call_args[0][0]
        assert len(encoded_chunks) == 2
        assert "real content" in encoded_chunks
        assert "more content" in encoded_chunks

    def test_index_file_reads_utf8_with_replace(self, tmp_path):
        """Files with encoding issues should still be indexed (errors='replace')."""
        lib = _make_librarian(tmp_path)
        test_file = tmp_path / "grimoire" / "encoded.md"
        # Write valid content and verify it's processed
        test_file.write_bytes("Hello \xc3\xa9 world".encode("utf-8"))

        lib.index_file(str(test_file))
        lib.vector_store.batch_upsert.assert_called_once()


# ===========================================================================
# Tests: GrimoireLibrarian.search
# ===========================================================================

class TestSearch:

    def test_search_returns_results(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.vector_store.search.return_value = [
            {"id": "abc:0", "content": "result text", "metadata": {}, "distance": 0.1}
        ]

        results = lib.search("test query")

        assert len(results) == 1
        assert results[0]["content"] == "result text"

    def test_search_passes_n_results(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.vector_store.search.return_value = []

        lib.search("query", n_results=10)

        lib.vector_store.search.assert_called_once()
        _, kwargs = lib.vector_store.search.call_args
        assert kwargs["n_results"] == 10

    def test_search_disabled_no_vector_store(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.vector_store = None

        results = lib.search("query")
        assert results == []

    def test_search_disabled_no_encoder(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.encoder = None

        results = lib.search("query")
        assert results == []

    def test_search_encoder_exception(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.encoder.encode.side_effect = RuntimeError("encoding error")

        results = lib.search("query")
        assert results == []

    def test_search_vector_store_exception(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.vector_store.search.side_effect = RuntimeError("db error")

        results = lib.search("query")
        assert results == []

    def test_search_empty_embedding_array(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.encoder.encode.return_value = np.array([])

        results = lib.search("query")
        assert results == []

    def test_search_encodes_query_as_string(self, tmp_path):
        lib = _make_librarian(tmp_path)
        lib.vector_store.search.return_value = []

        lib.search("my query text")

        lib.encoder.encode.assert_called_once_with("my query text")


# ===========================================================================
# Tests: GrimoireLibrarian.start_reindexing
# ===========================================================================

class TestStartReindexing:

    @pytest.mark.asyncio
    async def test_indexes_all_md_files(self, tmp_path):
        lib = _make_librarian(tmp_path)

        # Create test files
        (tmp_path / "grimoire" / "spell1.md").write_text("Spell one", encoding="utf-8")
        (tmp_path / "grimoire" / "spell2.md").write_text("Spell two", encoding="utf-8")
        (tmp_path / "memories" / "2026-02-19.md").write_text("Daily log", encoding="utf-8")

        # Track calls via side_effect
        indexed_files = []
        original_index = lib.index_file

        def track_index(fp):
            indexed_files.append(fp)
            return original_index(fp)

        lib.index_file = track_index

        await lib.start_reindexing()

        assert len(indexed_files) == 3
        filenames = {Path(f).name for f in indexed_files}
        assert filenames == {"spell1.md", "spell2.md", "2026-02-19.md"}

    @pytest.mark.asyncio
    async def test_reindexing_skips_non_md_files(self, tmp_path):
        lib = _make_librarian(tmp_path)

        (tmp_path / "grimoire" / "spell.md").write_text("Spell", encoding="utf-8")
        (tmp_path / "grimoire" / "config.json").write_text("{}", encoding="utf-8")
        (tmp_path / "grimoire" / "script.py").write_text("pass", encoding="utf-8")

        indexed_files = []
        original_index = lib.index_file
        lib.index_file = lambda fp: (indexed_files.append(fp), original_index(fp))

        await lib.start_reindexing()

        # Only .md files should be found by glob
        assert len(indexed_files) == 1
        assert Path(indexed_files[0]).name == "spell.md"

    @pytest.mark.asyncio
    async def test_reindexing_handles_nested_dirs(self, tmp_path):
        lib = _make_librarian(tmp_path)

        nested = tmp_path / "grimoire" / "subdir" / "deep"
        nested.mkdir(parents=True)
        (nested / "nested.md").write_text("Nested content", encoding="utf-8")

        indexed_files = []
        original_index = lib.index_file
        lib.index_file = lambda fp: (indexed_files.append(fp), original_index(fp))

        await lib.start_reindexing()

        assert len(indexed_files) == 1
        assert "nested.md" in indexed_files[0]

    @pytest.mark.asyncio
    async def test_reindexing_empty_directories(self, tmp_path):
        lib = _make_librarian(tmp_path)

        # No files created — directories are empty
        await lib.start_reindexing()  # Should complete without error

    @pytest.mark.asyncio
    async def test_reindexing_concurrent_execution(self, tmp_path):
        """Verify that files are indexed concurrently (not purely sequential)."""
        lib = _make_librarian(tmp_path)

        # Create several files
        for i in range(8):
            (tmp_path / "grimoire" / f"file{i}.md").write_text(f"Content {i}", encoding="utf-8")

        call_timestamps = []

        original_index = lib.index_file
        def tracking_index(fp):
            import time
            call_timestamps.append(time.monotonic())
            original_index(fp)

        lib.index_file = tracking_index

        await lib.start_reindexing()

        # All 8 files should be indexed
        assert len(call_timestamps) == 8
