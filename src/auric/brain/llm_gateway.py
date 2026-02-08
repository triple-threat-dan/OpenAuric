
import asyncio
import logging
from typing import List, Dict, Any, Optional

import litellm
import time
from auric.core.config import AuricConfig
from auric.core.database import AuditLogger, LLMInteraction

logger = logging.getLogger("auric.brain.gateway")

class LLMGateway:
    """
    The Patron Interface (LLM Gateway) for OpenAuric.
    
    Abstacts interactions with AI models (Gemini, OpenAI, Anthropic, Local Ollama).
    Enforces resource constraints for local hardware by serializing requests.
    """

    def __init__(self, config: AuricConfig, audit_logger: Optional[AuditLogger] = None):
        self.config = config
        self.audit_logger = audit_logger
        self._local_semaphore = asyncio.Semaphore(1)
        
        # Cache model names from config for easy access
        self.smart_model_id = config.agents.smart_model
        self.fast_model_id = config.agents.fast_model
        
        # Store keys for access during completion
        self.keys = config.keys
        
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
        
        # Determine API key based on provider
        api_key = None
        try:
            # We try to guess user intent if they provided a key in config
            # We try to guess user intent if they provided a key in config
            if self.keys.openrouter and "openrouter" in model:
                api_key = self.keys.openrouter
            elif self.keys.openai and "gpt" in model:
                api_key = self.keys.openai
            elif self.keys.anthropic and "claude" in model:
                api_key = self.keys.anthropic
            elif self.keys.gemini and "gemini" in model:
                api_key = self.keys.gemini
        except Exception:
            # Fallback to env vars handled by litellm
            pass
        
        try:
            start_time = time.time()
            response = None
            
            if check_local:
                logger.debug(f"Acquiring local semaphore for model: {model}")
                async with self._local_semaphore:
                    logger.debug(f"Calling local model: {model}")
                    response = await litellm.acompletion(
                        model=model,
                        messages=messages,
                        api_key=api_key,
                        **kwargs
                    )
            else:
                # Concurrent execution for remote models
                logger.debug(f"Calling remote model: {model}")
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    api_key=api_key,
                    **kwargs
                )
            
            duration = (time.time() - start_time) * 1000
            
            # Log Interaction
            if self.audit_logger and response:
                try:
                    usage = response.usage if hasattr(response, "usage") else None
                    p_tokens = usage.prompt_tokens if usage else 0
                    c_tokens = usage.completion_tokens if usage else 0
                    cost = response._hidden_params.get("response_cost", 0.0) if hasattr(response, "_hidden_params") else 0.0
                    
                    content = response.choices[0].message.content if response.choices else ""
                    
                    interaction = LLMInteraction(
                        model=model,
                        input_messages=messages, # Pydantic will serialize this to Json
                        output_content=content or "",
                        prompt_tokens=p_tokens,
                        completion_tokens=c_tokens,
                        total_cost=cost,
                        duration_ms=duration
                    )
                    await self.audit_logger.log_llm(interaction)
                except Exception as log_err:
                    logger.error(f"Failed to log LLM interaction: {log_err}")

            return response
                
        except Exception as e:
            logger.error(f"LLM Gateway Error ({model}): {e}")
            raise
