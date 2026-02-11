
import asyncio
import time
import sys
import os
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from auric.core.config import AuricConfig, AgentsConfig
from auric.brain.llm_gateway import LLMGateway
import litellm

async def mock_acompletion(*args, **kwargs):
    model = kwargs.get('model')
    print(f"  [Mock] Started request for {model}...")
    await asyncio.sleep(1.0) # Simulate delay
    print(f"  [Mock] Finished request for {model}!")
    return {"choices": [{"message": {"content": "Mock response"}}]}

async def test_gateway():
    print("=== Testing LLM Gateway Concurrency ===")
    
    # Setup Config
    config = AuricConfig()
    config.agents.smart_model = "ollama/llama3" # Local
    config.agents.fast_model = "gemini/gemini-1.5-flash" # Remote
    
    gateway = LLMGateway(config)
    
    # Patch litellm
    with patch('litellm.acompletion', side_effect=mock_acompletion):
        
        # Test 1: Local Serialization
        print("\n--- Test 1: Local Model (Should be Serialized ~2s) ---")
        start_time = time.time()
        
        task1 = asyncio.create_task(gateway.chat_completion([], tier="smart"))
        task2 = asyncio.create_task(gateway.chat_completion([], tier="smart"))
        
        await asyncio.gather(task1, task2)
        
        duration = time.time() - start_time
        print(f"Duration: {duration:.2f}s")
        if duration >= 1.9:
            print("SUCCESS: Local requests were serialized.")
        else:
            print("FAILURE: Local requests ran concurrently!")

        # Test 2: Remote Concurrency
        print("\n--- Test 2: Remote Model (Should be Concurrent ~1s) ---")
        start_time = time.time()
        
        task3 = asyncio.create_task(gateway.chat_completion([], tier="fast"))
        task4 = asyncio.create_task(gateway.chat_completion([], tier="fast"))
        
        await asyncio.gather(task3, task4)
        
        duration = time.time() - start_time
        print(f"Duration: {duration:.2f}s")
        if duration < 1.5:
             print("SUCCESS: Remote requests ran concurrently.")
        else:
             print("FAILURE: Remote requests were serialized!")

if __name__ == "__main__":
    asyncio.run(test_gateway())
