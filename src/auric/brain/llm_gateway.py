
import asyncio
import logging
from typing import List, Dict, Any, Optional

import litellm
from auric.core.config import AuricConfig

logger = logging.getLogger("auric.brain.gateway")

class LLMGateway:
    """
    The Patron Interface (LLM Gateway) for OpenAuric.
    
    Abstacts interactions with AI models (Gemini, OpenAI, Anthropic, Local Ollama).
    Enforces resource constraints for local hardware by serializing requests.
    """

    def __init__(self, config: AuricConfig):
        self.config = config
        self._local_semaphore = asyncio.Semaphore(1)
        
        # Cache model names from config for easy access
        self.smart_model_id = config.agents.smart_model
        self.fast_model_id = config.agents.fast_model
        
        # We can also check the global 'is_local' flag from config, 
        # but per-model checks are more robust if mixing local/remote.
        self.is_global_local = config.agents.is_local

    def is_local_model(self, model_name: str) -> bool:
        """
        Determines if a model needs to be serialized due to local resource constraints.
        """
        model_lower = model_name.lower()
        
        # Explicit config override
        if self.is_global_local:
            return True
            
        # Common local model prefixes
        if model_lower.startswith("ollama/") or model_lower.startswith("local/"):
            return True
            
        return False

    async def chat_completion(
        self, 
        messages: List[Dict[str, str]], 
        tier: str = "smart", 
        **kwargs
    ) -> Any:
        """
        Unified interface for chat completions.
        
        Args:
            messages: List of message dictionaries (role, content).
            tier: "smart" (high reasoning) or "fast" (low latency).
            **kwargs: Additional arguments passed to litellm (temperature, etc.)
        """
        
        # Resolve model
        if tier == "fast":
            model = self.fast_model_id
        else:
            model = self.smart_model_id
            
        check_local = self.is_local_model(model)
        
        try:
            if check_local:
                logger.debug(f"Acquiring local semaphore for model: {model}")
                async with self._local_semaphore:
                    logger.debug(f"Calling local model: {model}")
                    response = await litellm.acompletion(
                        model=model,
                        messages=messages,
                        **kwargs
                    )
                    return response
            else:
                # Concurrent execution for remote models
                logger.debug(f"Calling remote model: {model}")
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    **kwargs
                )
                return response
                
        except Exception as e:
            logger.error(f"LLM Gateway Error ({model}): {e}")
            raise
