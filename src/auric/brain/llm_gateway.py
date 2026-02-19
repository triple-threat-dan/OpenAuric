
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
        # Now using the new dictionary structure
        self.models_config = config.agents.models
        
        # Store keys for access during completion
        self.keys = config.keys
        
        # We can also check the global 'is_local' flag from config, 
        # but per-model checks are more robust if mixing local/remote.
        self.is_global_local = config.agents.is_local

        # Suppress noisy LiteLLM logs
        litellm.suppress_debug_info = True

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
        session_id: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        Unified interface for chat completions.
        
        Args:
            messages: List of message dictionaries (role, content).
            tier: "smart" (high reasoning) or "fast" (low latency).
            **kwargs: Additional arguments passed to litellm (temperature, etc.)
        """
        
        # Resolve model from config
        model_config = self.models_config.get(tier)
        if not model_config:
            logger.warning(f"Model tier '{tier}' not found in config. Defaulting to 'smart_model'.")
            model_config = self.models_config.get("smart_model")
            
        if not model_config:
             raise ValueError(f"No model configuration found for tier '{tier}' and no default available.")

        if not model_config.enabled:
             raise ValueError(f"Model tier '{tier}' is disabled in configuration.")

        model = model_config.model
        provider = model_config.provider
            
        check_local = self.is_local_model(model)
        
        # Determine API key based on provider
        api_key = None
        try:
            # Pydantic model dump for keys
            keys_dict = self.keys.model_dump(exclude_none=True)
            
            # 1. Direct provider match from config
            if provider in keys_dict:
                 api_key = keys_dict[provider]
            
            # 2. Fallback heuristic (legacy support if provider is generic like "openai" but model is "gpt-4")
            if not api_key:
                if "openrouter" in provider or "openrouter" in model:
                    api_key = self.keys.openrouter
                elif "openai" in provider or "gpt" in model:
                    api_key = self.keys.openai
                elif "anthropic" in provider or "claude" in model:
                    api_key = self.keys.anthropic
                elif "gemini" in provider or "gemini" in model:
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
                    
                    # Serialize input messages ensuring they are pure dicts
                    serialized_messages = []
                    for msg in messages:
                        if isinstance(msg, dict):
                            serialized_messages.append(msg)
                        elif hasattr(msg, "model_dump"):
                            serialized_messages.append(msg.model_dump())
                        elif hasattr(msg, "to_dict"):
                            serialized_messages.append(msg.to_dict())
                        else:
                            # Fallback
                            try:
                                serialized_messages.append(dict(msg))
                            except Exception:
                                serialized_messages.append({"role": "unknown", "content": str(msg)})

                    interaction = LLMInteraction(
                        model=model,
                        input_messages=serialized_messages, # Pydantic will serialize this to Json
                        output_content=content or "",
                        prompt_tokens=p_tokens,
                        completion_tokens=c_tokens,
                        total_cost=cost,
                        duration_ms=duration,
                        session_id=session_id
                    )
                    await self.audit_logger.log_llm(interaction)
                except Exception as log_err:
                    logger.error(f"Failed to log LLM interaction: {log_err}")

            return response
                
        except Exception as e:
            # Handle MALFORMED_FUNCTION_CALL from Gemini via OpenRouter.
            # The LLM returns finish_reason='error' which litellm can't parse,
            # but the response body often contains usable text content.
            error_str = str(e)
            if "MALFORMED_FUNCTION_CALL" in error_str or ("finish_reason" in error_str and "'error'" in error_str):
                logger.warning(f"LLM returned malformed function call ({model}). Attempting content recovery.")
                
                # Try to extract content from the error's embedded response
                recovered_content = self._recover_content_from_error(error_str)
                if recovered_content:
                    logger.info(f"Recovered content from malformed response: {recovered_content[:80]}...")
                    # Build a minimal synthetic response
                    return self._build_synthetic_response(recovered_content, model)
                    
                logger.warning(f"Could not recover content. Returning fallback.")
                return self._build_synthetic_response(
                    "I encountered a temporary issue with the AI provider. Please try again.", 
                    model
                )
            
            logger.error(f"LLM Gateway Error ({model}): {e}")
            raise

    @staticmethod
    def _recover_content_from_error(error_str: str) -> Optional[str]:
        """
        Attempts to extract usable text content from a litellm error that contains
        the raw OpenRouter response (e.g. MALFORMED_FUNCTION_CALL errors).
        """
        import re
        import json
        
        # The error message embeds the raw response JSON. Try to find the content field.
        # Pattern: 'content': 'some text here'
        match = re.search(r"'content':\s*'([^']+)'", error_str)
        if match:
            content = match.group(1).strip()
            if content and content not in ('', 'None', 'null'):
                return content
        
        # Also try double-quoted JSON pattern 
        match = re.search(r'"content":\s*"([^"]+)"', error_str)
        if match:
            content = match.group(1).strip()
            if content and content not in ('', 'None', 'null'):
                return content
                
        return None

    @staticmethod
    def _build_synthetic_response(content: str, model: str):
        """
        Builds a minimal litellm-compatible response object for recovered content.
        """
        from litellm import ModelResponse
        from litellm.types.utils import Choices, Message, Usage
        import uuid
        
        return ModelResponse(
            id=f"chatcmpl-recovered-{uuid.uuid4().hex[:12]}",
            model=model,
            choices=[Choices(
                finish_reason="stop",
                index=0,
                message=Message(content=content, role="assistant")
            )],
            usage=Usage(completion_tokens=0, prompt_tokens=0, total_tokens=0)
        )
