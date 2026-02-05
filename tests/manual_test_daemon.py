
import asyncio
import sys
import logging
from unittest.mock import MagicMock
from fastapi import FastAPI

# Add src to path so we can import auric
sys.path.append("src")

from auric.core.daemon import run_daemon

# Configure logging to see output
logging.basicConfig(level=logging.INFO)

async def test_daemon_lifecycle():
    print(">>> TEST: Starting Daemon Lifecycle Test")
    
    # Mock Textual App
    # We need an object with run_async() that is awaitable
    class MockTUI:
        async def run_async(self):
            print(">>> MOCK TUI: Running... simulating UI interaction for 2 seconds")
            await asyncio.sleep(2)
            print(">>> MOCK TUI: Exiting...")

    tui_app = MockTUI()
    api_app = FastAPI()

    try:
        await run_daemon(tui_app, api_app)
        print(">>> TEST: Daemon exited cleanly")
    except Exception as e:
        print(f">>> TEST FAILED: Daemon raised exception: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(test_daemon_lifecycle())
    except KeyboardInterrupt:
        pass
