
import asyncio
import logging
import time
import re
import uuid
from typing import List, Dict, Any, Optional

import litellm
from litellm import ModelResponse
from litellm.types.utils import Choices, Message, Usage

from auric.core.config import AuricConfig
from auric.core.database import AuditLogger, LLMInteraction

logger = logging.getLogger("auric.brain.gateway")

# Pre-compile regex usage for error recovery
RE_CONTENT_SINGLE = re.compile(r"'content':\s*'([^']+)'")
RE_CONTENT_DOUBLE = re.compile(r'"content":\s*"([^"]+)"')

class LLMGateway:
    """
    The Patron Interface (LLM Gateway) for OpenAuric.
    
    Abstracts interactions with AI models (Gemini, OpenAI, Anthropic, Local Ollama).
    Enforces resource constraints for local hardware by serializing requests.
    """

    def __init__(self, config: AuricConfig, audit_logger: Optional[AuditLogger] = None):
        self.config = config
        self.audit_logger = audit_logger
        self._local_semaphore = asyncio.Semaphore(1)
        
        self.models_config = config.agents.models
        self.keys = config.keys
        self.is_global_local = config.agents.is_local

        # Suppress noisy LiteLLM logs
        litellm.suppress_debug_info = True

    def is_local_model(self, model_name: str) -> bool:
        """
        Determines if a model needs to be serialized due to local resource constraints.
        """
        if self.is_global_local:
            return True
            
        return model_name.lower().startswith(("ollama/", "local/"))

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
        model_config = self.models_config.get(tier) or self.models_config.get("smart_model")
            
        if not model_config:
             raise ValueError(f"No model configuration found for tier '{tier}' and no default available.")

        if not model_config.enabled:
             raise ValueError(f"Model tier '{tier}' is disabled in configuration.")

        model = model_config.model
        provider = model_config.provider
        check_local = self.is_local_model(model)
        
        # Determine API key based on provider
        api_key = self._resolve_api_key(provider, model)
        
        try:
            start_time = time.time()
            
            if check_local:
                logger.debug(f"Acquiring local semaphore for model: {model}")
                async with self._local_semaphore:
                    response = await self._call_model(model, messages, api_key, **kwargs)
            else:
                response = await self._call_model(model, messages, api_key, **kwargs)
            
            duration = (time.time() - start_time) * 1000
            
            # Log Interaction
            if self.audit_logger:
                await self._log_interaction(response, messages, model, duration, session_id)

            return response
                
        except Exception as e:
            return self._handle_error(e, model)

    async def _call_model(self, model: str, messages: List[Dict], api_key: Optional[str], **kwargs) -> Any:
        """Executes the actual model call via litellm."""
        logger.debug(f"Calling model: {model}")
        return await litellm.acompletion(
            model=model,
            messages=messages,
            api_key=api_key,
            num_retries=3,
            **kwargs
        )

    def _resolve_api_key(self, provider: str, model: str) -> Optional[str]:
        """Resolves the API key using config and heuristics."""
        # 1. Direct provider match from config
        if hasattr(self.keys, provider):
            key = getattr(self.keys, provider)
            if key: return key
            
        # 2. Fallback heuristics
        if "openrouter" in provider or "openrouter" in model:
            return self.keys.openrouter
        if "openai" in provider or "gpt" in model:
            return self.keys.openai
        if "anthropic" in provider or "claude" in model:
            return self.keys.anthropic
        if "gemini" in provider or "gemini" in model:
            return self.keys.gemini
            
        return None

    async def _log_interaction(self, response: Any, messages: List[Dict], model: str, duration: float, session_id: Optional[str]):
        """Logs the LLM interaction to the audit logger."""
        if not response: 
            return

        try:
            usage = getattr(response, "usage", None)
            p_tokens = usage.prompt_tokens if usage else 0
            c_tokens = usage.completion_tokens if usage else 0
            
            cost = 0.0
            if hasattr(response, "_hidden_params"):
                cost = response._hidden_params.get("response_cost", 0.0)
            
            content = response.choices[0].message.content if response.choices else ""
            
            # Serialize input messages ensuring they are pure dicts
            serialized_messages = [
                msg if isinstance(msg, dict) else 
                msg.model_dump() if hasattr(msg, "model_dump") else 
                msg.to_dict() if hasattr(msg, "to_dict") else 
                dict(msg) # fallback
                for msg in messages
            ]

            interaction = LLMInteraction(
                model=model,
                input_messages=serialized_messages,
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

    def _handle_error(self, e: Exception, model: str):
        """Handles exceptions and attempts recovery for malformed responses."""
        error_str = str(e)
        if "MALFORMED_FUNCTION_CALL" in error_str or ("finish_reason" in error_str and "'error'" in error_str):
            logger.warning(f"LLM returned malformed function call ({model}). Attempting content recovery.")
            
            recovered_content = self._recover_content_from_error(error_str)
            if recovered_content:
                logger.info(f"Recovered content from malformed response: {recovered_content[:80]}...")
                return self._build_synthetic_response(recovered_content, model)
                
            logger.warning(f"Could not recover content. Returning fallback.")
            return self._build_synthetic_response(
                "I encountered a temporary issue with the AI provider. Please try again.", 
                model
            )
        
        logger.error(f"LLM Gateway Error ({model}): {e}")
        raise e

    @staticmethod
    def _recover_content_from_error(error_str: str) -> Optional[str]:
        """
        Attempts to extract usable text content from a litellm error.
        """
        for pattern in (RE_CONTENT_SINGLE, RE_CONTENT_DOUBLE):
            match = pattern.search(error_str)
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
