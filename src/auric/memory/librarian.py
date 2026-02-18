import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any, Callable
import concurrent.futures

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent, FileMovedEvent

from auric.core.config import AURIC_ROOT, load_config
from .chroma_store import ChromaStore
from .embeddings import EmbeddingWrapper

logger = logging.getLogger("auric.librarian")

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
        if path.suffix not in (".md", ".txt"): # Added .txt just in case
            return True
        return False

    def on_moved(self, event: FileMovedEvent) -> None:
        if event.is_directory: return
        self._dispatch_update(event.dest_path)
        # TODO: Handle deletion of src_path if needed, but for now we focus on new content.

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
        if file_path in self.active_tasks:
            task = self.active_tasks[file_path]
            if not task.done():
                task.cancel()
        
        self.active_tasks[file_path] = self.loop.create_task(
            self._debounce_and_index(file_path)
        )

    async def _debounce_and_index(self, file_path: str) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            # Run callback (indexing) in a separate thread to avoid blocking loop
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
        if grimoire_path is None:
            self.grimoire_path = AURIC_ROOT / "grimoire"
        else:
            self.grimoire_path = grimoire_path
            
        if memories_path is None:
            self.memories_path = AURIC_ROOT / "memories"
        else:
            self.memories_path = memories_path

        self.observer: Optional[Observer] = None
        self.event_handler: Optional[GrimoireHandler] = None
        
        # Initialize Vector Store
        self.vector_store = None
        try:
            self.vector_store = ChromaStore()
        except Exception as e:
            logger.error(f"Librarian: Failed to initialize Vector Store: {e}")
            logger.warning("Librarian: RAG capabilities will be disabled.")
        
        # Initialize Embedding Model
        self.encoder = None
        if self.vector_store:
            try:
                config = load_config()
                # EmbeddingWrapper handles logging
                self.encoder = EmbeddingWrapper(config)
            except Exception as e:
                logger.error(f"Librarian: Failed to load embedding model: {e}")
                self.vector_store = None # Disable if no encoder

    def start(self) -> None:
        """
        Initializes and starts the directory observer.
        """
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
            self.observer.stop() # stop() then join()
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
        
        # Check if file was deleted
        if not path.exists():
            logger.info(f"Librarian: File deleted, removing from index: {path.name}")
            self.vector_store.delete_by_metadata({"source": str(path)})
            return

        try:
            logger.debug(f"Librarian: Indexing {path.name}...")
            content = path.read_text(encoding="utf-8", errors="replace")
            
            # Simple chunking
            chunks = self._chunk_text(content)
            
            # Remove old entries for this file before re-inserting
            self.vector_store.delete_by_metadata({"source": str(path)})
            
            if not chunks:
                return

            # Batch encode
            embeddings = self.encoder.encode(chunks).tolist()
            
            for i, chunk in enumerate(chunks):
                if not chunk.strip(): continue
                
                chunk_id = f"{path.name}:{i}" # potentially redundant if path isn't unique across dirs, but simple
                # For better uniqueness use hash or relative path
                # Let's use relative path if possible, or full path hash
                # Using full path as source, so let's use full path hash for id prefix
                import hashlib
                path_hash = hashlib.md5(str(path).encode()).hexdigest()
                chunk_id = f"{path_hash}:{i}"
                
                self.vector_store.upsert(
                    id=chunk_id,
                    content=chunk,
                    metadata={"source": str(path), "chunk_index": i, "filename": path.name},
                    embedding=embeddings[i]
                )
            
            logger.info(f"Librarian: Indexed {path.name} ({len(chunks)} chunks)")
            
        except Exception as e:
            logger.error(f"Librarian: Failed to index {file_path}: {e}")

    def _chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 100) -> List[str]:
        """
        Simple text chunking with overlap.
        """
        if len(text) < chunk_size:
            return [text]
            
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end]
            chunks.append(chunk)
            
            if end == len(text):
                break
                
            start += (chunk_size - overlap)
            
        return chunks

    def search(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """
        Semantic search using the vector store.
        """
        if not self.vector_store or not self.encoder:
            return []

        try:
            # Encoder returns (1, D) array for single string, need flat list (D,)
            embedding_array = self.encoder.encode(query)
            if len(embedding_array) > 0:
                embedding = embedding_array[0].tolist()
            else:
                return []
                
            return self.vector_store.search(embedding, n_results=n_results)
        except Exception as e:
            logger.error(f"Librarian search failed: {e}")
            return []

    async def start_reindexing(self) -> None:
        """
        Scans and indexes all files in the Grimoire and Memories.
        Runs in background to avoid blocking startup.
        """
        logger.info("Librarian: Starting full re-indexing...")
        loop = asyncio.get_running_loop()
        
        # Recursive glob
        files = list(self.grimoire_path.glob("**/*.md"))
        files.extend(list(self.memories_path.glob("**/*.md")))

        logger.info(f"Librarian: Function found {len(files)} files to index.")
        
        for file_path in files:
            # Run in executor
            await loop.run_in_executor(None, self.index_file, str(file_path))
            
        logger.info("Librarian: Full re-indexing complete.")
