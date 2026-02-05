import asyncio
import tempfile
import shutil
import logging
from pathlib import Path
from auric.memory.librarian import GrimoireLibrarian

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def main():
    # 1. Create a temporary directory for testing
    temp_dir = Path(tempfile.mkdtemp(prefix="auric_test_"))
    print(f"Created temp dir: {temp_dir}")
    
    try:
        # 2. Initialize Librarian pointing to temp dir
        librarian = GrimoireLibrarian(grimoire_path=temp_dir)
        librarian.start()

        # 3. Simulate Streaming Write
        test_file = temp_dir / "streaming_doc.md"
        print("--- Simulating streaming write (5 writes over 0.5s) ---")
        
        # Write chunks rapidly
        for i in range(5):
            with open(test_file, "a") as f:
                f.write(f"Chunk {i}\n")
            print(f"Wrote chunk {i}")
            await asyncio.sleep(0.1)
        
        print("--- Writes done. Waiting 3 seconds (Debounce is 2s) ---")
        print("Expect: ONLY ONE 'Re-indexing' log below.")
        
        await asyncio.sleep(3.0)

        # 4. Simulate Separate Edit
        print("\n--- Simulating new distinct edit ---")
        with open(test_file, "a") as f:
            f.write("New distinct edit\n")
        print("Wrote new edit. Waiting 3 seconds.")
        
        await asyncio.sleep(3.0)
        
        # 5. Stop
        librarian.stop()

    finally:
        # Cleanup
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        print("Temp dir cleaned up.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
