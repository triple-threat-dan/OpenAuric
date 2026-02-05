import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent, FileMovedEvent

logger = logging.getLogger("auric.librarian")

class GrimoireHandler(FileSystemEventHandler):
    """
    Handles file system events for the Grimoire.
    Debounces events to prevent excessive re-indexing during streaming writes.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, debounce_seconds: float = 2.0):
        self.loop = loop
        self.debounce_seconds = debounce_seconds
        # Maps file path to the pending asyncio.Task
        self.active_tasks: Dict[str, asyncio.Task] = {}

    def _should_ignore(self, file_path: str) -> bool:
        """
        Returns True if the file should be ignored.
        Ignores:
        - Hidden files (starting with .)
        - Non-markdown (.md) and non-python (.py) files
        """
        path = Path(file_path)
        if path.name.startswith("."):
            return True
        
        if path.suffix not in (".md", ".py"):
            return True
            
        return False

    def on_moved(self, event: FileMovedEvent) -> None:
        if event.is_directory:
            return
        
        # Handle both source and destination if needed, 
        # but primarily we care about the new content at dest_path.
        # Ideally we might want to handle deletion of src_path too, 
        # but for this ticket we focus on re-indexing content.
        self._dispatch_update(event.dest_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._dispatch_update(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._dispatch_update(event.src_path)

    def _dispatch_update(self, file_path: str) -> None:
        """
        Thread-safe dispatch to the asyncio loop.
        """
        if self._should_ignore(file_path):
            return

        # watchdog runs in a separate thread, so we must schedule the 
        # debounce logic on the main asyncio loop.
        self.loop.call_soon_threadsafe(self._schedule_debounce, file_path)

    def _schedule_debounce(self, file_path: str) -> None:
        """
        Runs on the main event loop. Cancels existing timer and starts a new one.
        """
        # 1. Cancel existing task if it exists
        if file_path in self.active_tasks:
            task = self.active_tasks[file_path]
            if not task.done():
                task.cancel()
        
        # 2. Schedule new task
        self.active_tasks[file_path] = self.loop.create_task(
            self._debounce_and_index(file_path)
        )

    async def _debounce_and_index(self, file_path: str) -> None:
        """
        Waits for the debounce interval, then triggers indexing.
        """
        try:
            await asyncio.sleep(self.debounce_seconds)
            self._trigger_reindexing(file_path)
        except asyncio.CancelledError:
            # Task was cancelled because a new event came in or shutdown
            pass
        finally:
            # Clean up the task reference if it's still us
            if self.active_tasks.get(file_path) == asyncio.current_task():
                del self.active_tasks[file_path]

    def shutdown(self) -> None:
        """
        Cancels all pending debounce tasks.
        """
        for task in self.active_tasks.values():
            if not task.done():
                task.cancel()
        self.active_tasks.clear()

    def _trigger_reindexing(self, file_path: str) -> None:
        """
        Placeholder for the actual Vector DB embedding logic.
        """
        logger.info(f"Librarian: Re-indexing {file_path}...")
        print(f"Librarian: Re-indexing {file_path}...")


class GrimoireLibrarian:
    """
    Service responsible for monitoring the Grimoire (agent memory) directory
    and triggering updates when files change.
    """

    def __init__(self, grimoire_path: Optional[Path] = None):
        """
        Args:
            grimoire_path: Path to the directory to watch. 
                           Defaults to ~/.auric/grimoire
        """
        if grimoire_path is None:
            self.grimoire_path = Path.home() / ".auric" / "grimoire"
        else:
            self.grimoire_path = grimoire_path

        self.observer: Optional[Observer] = None
        self.event_handler: Optional[GrimoireHandler] = None

    def start(self) -> None:
        """
        Initializes and starts the directory observer.
        """
        if not self.grimoire_path.exists():
            logger.warning(f"Grimoire directory {self.grimoire_path} does not exist. Creating it.")
            self.grimoire_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Librarian starting watch on {self.grimoire_path}")

        # Get the current running loop to pass to the handler
        # This assumes start() is called from the main async function
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("Librarian must be started within a running asyncio loop.")
            return

        self.event_handler = GrimoireHandler(loop=loop)
        
        self.observer = Observer()
        self.observer.schedule(self.event_handler, str(self.grimoire_path), recursive=True)
        self.observer.start()
        logger.info("Librarian observer started.")

    def stop(self) -> None:
        """
        Cleanly stops the observer.
        """
        if self.event_handler:
            self.event_handler.shutdown()
            
        if self.observer:
            logger.info("Librarian stopping observer...")
            self.observer.stop()
            self.observer.join()
            logger.info("Librarian observer stopped.")
            self.observer = None
