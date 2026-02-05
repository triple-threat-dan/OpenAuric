"""
The Matrix TUI: A terminal-based interface for OpenAuric.
"""
import asyncio
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Input, Static, Markdown, RichLog
from textual import work
from rich.text import Text

from auric.memory.focus_manager import FocusManager, FocusModel

class AuricTUI(App):
    """The main TUI Application class."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #main-container {
        height: 1fr;
    }

    #left-pane {
        width: 60%;
        height: 100%;
        border-right: heavy $accent;
    }

    #right-pane {
        width: 40%;
        height: 100%;
    }

    #log {
        height: 100%;
        background: $surface;
        color: $text;
    }

    #focus {
        height: 100%;
        padding: 1;
        background: $surface-lighten-1;
    }

    #stats-bar {
        height: 3;
        dock: bottom;
        border-top: solid $primary;
        background: $surface-darken-1;
        content-align: center middle;
    }

    Input {
        dock: bottom;
    }
    """

    BINDINGS = [
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, event_bus: asyncio.Queue, focus_file: Path):
        super().__init__()
        self.event_bus = event_bus
        self.focus_manager = FocusManager(focus_file)
        self.last_focus_content = ""

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        
        with Horizontal(id="main-container"):
            with Vertical(id="left-pane"):
                yield RichLog(id="log", wrap=True, highlight=True, markup=True)
            with Vertical(id="right-pane"):
                yield Markdown(id="focus")
        
        yield Static(id="stats-bar", content="Initializing System...")
        yield Input(placeholder="Enter command...", id="input")
        yield Footer()

    def on_mount(self) -> None:
        """Called when app starts."""
        self.title = "OpenAuric: The Recursive Agentic Warlock"
        
        # Start background workers
        self.poll_logs()
        self.watch_focus()
        self.update_stats()

    @work(exclusive=True)
    async def poll_logs(self) -> None:
        """Polls the event bus for new logs/thoughts."""
        log_widget = self.query_one("#log", RichLog)
        while True:
            # We peek or get from queue. 
            # Note: Queue.get wait for an item.
            # In a real app we might want non-blocking get or specific message types.
            # For now, we assume everything in event_bus is a printable string or dict.
            try:
                message = await self.event_bus.get()
                
                # Format message based on type
                if isinstance(message, dict):
                    # Basic structured logging
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    level = message.get("level", "INFO")
                    text = message.get("message", str(message))
                    
                    if level == "ERROR":
                        style = "bold red"
                    elif level == "WARNING":
                        style = "yellow"
                    elif level == "THOUGHT":
                        style = "italic cyan"
                    else:
                        style = "green"
                        
                    log_widget.write(Text(f"[{timestamp}] [{level}] {text}", style=style))
                else:
                    # Raw string
                    log_widget.write(str(message))
                
                self.event_bus.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_widget.write(f"[bold red]Error polling logs: {e}[/bold red]")

    @work(exclusive=True)
    async def watch_focus(self) -> None:
        """Periodically checks FOCUS.md for updates."""
        focus_widget = self.query_one("#focus", Markdown)
        
        while True:
            try:
                # We read the file directly for the TUI display to avoid locking issues in FocusManager
                # or we can use FocusManager.load() if it's safe.
                # Let's use the internal _read_from_file for raw markdown content for now
                # or just use the public load() and re-serialize.
                # Actually, displaying the raw markdown is better for the "Markdown" widget.
                
                content = self.focus_manager._read_from_file()
                
                if content != self.last_focus_content:
                    self.last_focus_content = content
                    await focus_widget.update(content)
                    
            except Exception:
                pass # failures to read shouldn't crash TUI
                
            await asyncio.sleep(2.0)

    @work(exclusive=True)
    async def update_stats(self) -> None:
        """Updates the system stats bar."""
        stats_widget = self.query_one("#stats-bar", Static)
        
        while True:
            # Placeholder stats
            # In future, pull from a stats service
            import psutil
            mem = psutil.virtual_memory()
            
            stats_text = f"RAM: {mem.percent}% | Active Model: Local (Llama-3) | Tokens: 0"
            stats_widget.update(stats_text)
            
            await asyncio.sleep(5.0)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input."""
        if event.value:
            # Push user message to event bus as a "USER_INPUT" event
            # TODO: Define a strict event schema
            msg = {
                "level": "USER",
                "message": event.value
            }
            await self.event_bus.put(msg)
            
            # Clear input
            event.input.value = ""
