import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any, Callable

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent, FileMovedEvent

from auric.core.config import AURIC_ROOT, load_config
from .chroma_store import ChromaStore
from .embeddings import EmbeddingWrapper

logger = logging.getLogger("auric.librarian")

# Maximum number of files to index concurrently during a full re-index.
_REINDEX_CONCURRENCY = 4


class GrimoireHandler(FileSystemEventHandler):
    """
    Handles file system events for the Grimoire.
    Debounces events and triggers indexing via callback.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, callback: Callable[[str], None], debounce_seconds: float = 2.0):
        self.loop = loop
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.active_tasks: Dict[str, asyncio.Task] = {}

    def _should_ignore(self, file_path: str) -> bool:
        path = Path(file_path)
        if path.name.startswith("."):
            return True
        if path.suffix not in (".md", ".txt"):
            return True
        return False

    def on_moved(self, event: FileMovedEvent) -> None:
        if event.is_directory: return
        self._dispatch_update(event.dest_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory: return
        self._dispatch_update(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory: return
        self._dispatch_update(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory: return
        self._dispatch_update(event.src_path)

    def _dispatch_update(self, file_path: str) -> None:
        if self._should_ignore(file_path):
            return
        self.loop.call_soon_threadsafe(self._schedule_debounce, file_path)

    def _schedule_debounce(self, file_path: str) -> None:
        existing = self.active_tasks.get(file_path)
        if existing and not existing.done():
            existing.cancel()

        self.active_tasks[file_path] = self.loop.create_task(
            self._debounce_and_index(file_path)
        )

    async def _debounce_and_index(self, file_path: str) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            await self.loop.run_in_executor(None, self.callback, file_path)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in indexing task for {file_path}: {e}")
        finally:
            if self.active_tasks.get(file_path) == asyncio.current_task():
                del self.active_tasks[file_path]

    def shutdown(self) -> None:
        for task in self.active_tasks.values():
            if not task.done():
                task.cancel()
        self.active_tasks.clear()


class GrimoireLibrarian:
    """
    Service responsible for monitoring the Grimoire and managing the Vector Store.
    """

    def __init__(self, grimoire_path: Optional[Path] = None, memories_path: Optional[Path] = None):
        self.grimoire_path = grimoire_path or (AURIC_ROOT / "grimoire")
        self.memories_path = memories_path or (AURIC_ROOT / "memories")

        self.observer: Optional[Observer] = None
        self.event_handler: Optional[GrimoireHandler] = None

        self.vector_store = None
        self.encoder = None

        try:
            self.vector_store = ChromaStore()
        except Exception as e:
            logger.error(f"Librarian: Failed to initialize Vector Store: {e}")
            logger.warning("Librarian: RAG capabilities will be disabled.")
            return

        try:
            self.encoder = EmbeddingWrapper(load_config())
        except Exception as e:
            logger.error(f"Librarian: Failed to load embedding model: {e}")
            self.vector_store = None

    def start(self) -> None:
        """Initializes and starts the directory observer."""
        if not self.grimoire_path.exists():
            logger.warning(f"Grimoire directory {self.grimoire_path} does not exist. Creating it.")
            self.grimoire_path.mkdir(parents=True, exist_ok=True)

        if not self.memories_path.exists():
            logger.warning(f"Memories directory {self.memories_path} does not exist. Creating it.")
            self.memories_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Librarian starting watch on {self.grimoire_path} and {self.memories_path}")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("Librarian must be started within a running asyncio loop.")
            return

        self.event_handler = GrimoireHandler(loop=loop, callback=self.index_file)

        self.observer = Observer()
        self.observer.schedule(self.event_handler, str(self.grimoire_path), recursive=True)
        self.observer.schedule(self.event_handler, str(self.memories_path), recursive=True)
        self.observer.start()
        logger.info("Librarian observer started.")

    def stop(self) -> None:
        if self.event_handler:
            self.event_handler.shutdown()
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("Librarian observer stopped.")
            self.observer = None

    def index_file(self, file_path: str) -> None:
        """
        Reads, chunks, embeds, and upserts the file.
        Executed in a thread executor.
        """
        if not self.vector_store or not self.encoder:
            return

        path = Path(file_path)

        if not path.exists():
            logger.info(f"Librarian: File deleted, removing from index: {path.name}")
            self.vector_store.delete_by_metadata({"source": str(path)})
            return

        try:
            logger.debug(f"Librarian: Indexing {path.name}...")
            content = path.read_text(encoding="utf-8", errors="replace")

            chunks = [c for c in self._chunk_text(content) if c.strip()]

            self.vector_store.delete_by_metadata({"source": str(path)})

            if not chunks:
                return

            embeddings = self.encoder.encode(chunks).tolist()
            path_hash = hashlib.md5(str(path).encode()).hexdigest()

            ids = [f"{path_hash}:{i}" for i in range(len(chunks))]
            metadatas = [{"source": str(path), "chunk_index": i, "filename": path.name} for i in range(len(chunks))]

            self.vector_store.batch_upsert(
                ids=ids,
                contents=chunks,
                metadatas=metadatas,
                embeddings=embeddings,
            )

            logger.info(f"Librarian: Indexed {path.name} ({len(chunks)} chunks)")

        except Exception as e:
            logger.error(f"Librarian: Failed to index {file_path}: {e}")

    def _chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
        """Text chunking with overlap respecting semantic boundaries (paragraphs, lines, words)."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            
            if end >= len(text):
                chunks.append(text[start:])
                break

            # Define the window where we look for a good split point
            # Look in the last 20% of the proposed chunk
            window_start = max(start, end - int(chunk_size * 0.2))
            
            # Find the best split point in order of preference: paragraph, line, word
            split_idx = text.rfind('\n\n', window_start, end)
            if split_idx == -1:
                split_idx = text.rfind('\n', window_start, end)
            if split_idx == -1:
                split_idx = text.rfind(' ', window_start, end)

            if split_idx != -1 and split_idx > start:
                # We found a semantic boundary
                chunks.append(text[start:split_idx].strip())
                # Move start to just after the split, minus the overlap
                # We ensure overlap doesn't push us backwards past our current start
                start = max(start + 1, split_idx - overlap)
            else:
                # Fallback: hard split
                chunks.append(text[start:end])
                start += (chunk_size - overlap)

        return chunks

    def search(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """Semantic search using the vector store."""
        if not self.vector_store or not self.encoder:
            return []

        try:
            embedding_array = self.encoder.encode(query)
            if len(embedding_array) == 0:
                return []

            return self.vector_store.search(embedding_array[0].tolist(), n_results=n_results)
        except Exception as e:
            logger.error(f"Librarian search failed: {e}")
            return []

    async def start_reindexing(self) -> None:
        """
        Scans and indexes all files in the Grimoire and Memories.
        Uses a bounded semaphore to index multiple files concurrently.
        """
        logger.info("Librarian: Starting full re-indexing...")
        loop = asyncio.get_running_loop()

        files = list(self.grimoire_path.glob("**/*.md"))
        files.extend(self.memories_path.glob("**/*.md"))

        logger.info(f"Librarian: Found {len(files)} files to index.")

        semaphore = asyncio.Semaphore(_REINDEX_CONCURRENCY)

        async def _index_with_limit(fp: Path) -> None:
            async with semaphore:
                await loop.run_in_executor(None, self.index_file, str(fp))

        await asyncio.gather(*[_index_with_limit(f) for f in files])

        logger.info("Librarian: Full re-indexing complete.")
