import logging
import os
from typing import List, Union

import numpy as np
import litellm
from sentence_transformers import SentenceTransformer

from auric.core.config import AuricConfig

logger = logging.getLogger("auric.embeddings")

_DEFAULT_MODELS = {
    "gemini": "models/text-embedding-004",
    "openai": "text-embedding-3-small",
    "local": "all-MiniLM-L6-v2",
}


class EmbeddingWrapper:
    """Unified wrapper for SentenceTransformer and LiteLLM embedding models."""

    def __init__(self, config: AuricConfig):
        self.config = config
        self.local_model: SentenceTransformer | None = None

        model_config = config.agents.models.get("embeddings_model")
        if model_config and model_config.enabled:
            self.provider = model_config.provider.lower()
            if self.provider == "google":
                self.provider = "gemini"
            self.model_name = model_config.model
        else:
            self.provider = config.embeddings.provider
            self.model_name = config.embeddings.model

        if self.provider == "auto":
            self._resolve_auto_provider()

        logger.info("Embeddings: provider='%s', model='%s'", self.provider, self.model_name)

        if config.keys.gemini:
            os.environ["GEMINI_API_KEY"] = config.keys.gemini
        if config.keys.openai:
            os.environ["OPENAI_API_KEY"] = config.keys.openai

        if self.provider == "local":
            if not self.model_name:
                self.model_name = _DEFAULT_MODELS["local"]
            try:
                self.local_model = SentenceTransformer(self.model_name)
            except Exception as e:
                logger.error("Embeddings: Failed to load local model '%s': %s", self.model_name, e)
                raise

    def _resolve_auto_provider(self):
        """Resolve 'auto' provider based on available API keys."""
        if self.config.keys.gemini:
            self.provider = "gemini"
        elif self.config.keys.openai:
            self.provider = "openai"
        else:
            self.provider = "local"

        if not self.model_name:
            self.model_name = _DEFAULT_MODELS[self.provider]

        logger.info("Embeddings: auto-resolved to %s (%s)", self.provider, self.model_name)

    def encode(self, sentences: Union[str, List[str]]) -> np.ndarray:
        """Generate embeddings for the given text(s). Returns a numpy array."""
        if isinstance(sentences, str):
            sentences = [sentences]

        if self.provider == "local":
            return self.local_model.encode(sentences)

        if self.provider in ("openai", "gemini"):
            try:
                model_id = self.model_name
                if self.provider == "gemini" and not model_id.startswith("gemini/"):
                    model_id = f"gemini/{model_id}"

                response = litellm.embedding(model=model_id, input=sentences)
                return np.array([d["embedding"] for d in response["data"]])
            except Exception as e:
                logger.error("Embeddings: Failed to get embeddings from %s: %s", self.provider, e)
                raise

        raise ValueError(f"Unknown embedding provider: {self.provider}")
