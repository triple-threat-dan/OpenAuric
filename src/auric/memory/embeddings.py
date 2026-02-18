import logging
from typing import List, Union, Optional
import numpy as np

import os
from sentence_transformers import SentenceTransformer
import litellm
from auric.core.config import AuricConfig

logger = logging.getLogger("auric.embeddings")

class EmbeddingWrapper:
    """
    Wrapper for embedding models that unifies SentenceTransformers and LiteLLM.
    """
    def __init__(self, config: AuricConfig):
        self.config = config
        
        # Check for new 'embeddings_model' in agents config
        model_config = config.agents.models.get("embeddings_model")
        
        if model_config and model_config.enabled:
            self.provider = model_config.provider.lower()
            if self.provider == "google": 
                self.provider = "gemini"
            self.model_name = model_config.model
        else:
            # Fallback to root embeddings config
            self.provider = config.embeddings.provider
            self.model_name = config.embeddings.model
        
        self.local_model: Optional[SentenceTransformer] = None
        
        # Auto-detect provider if needed
        if self.provider == "auto":
            self._resolve_auto_provider()
            
        logger.info(f"Embeddings: Initializing with provider='{self.provider}', model='{self.model_name}'")
        
        # Inject Keys into Env for LiteLLM
        if config.keys.gemini:
             os.environ["GEMINI_API_KEY"] = config.keys.gemini
        if config.keys.openai:
             os.environ["OPENAI_API_KEY"] = config.keys.openai
        
        if self.provider == "local":
            if not self.model_name:
                self.model_name = "all-MiniLM-L6-v2"
            try:
                self.local_model = SentenceTransformer(self.model_name)
            except Exception as e:
                logger.error(f"Embeddings: Failed to load local model '{self.model_name}': {e}")
                raise

    def _resolve_auto_provider(self):
        """Resolves 'auto' provider based on available keys."""
        if self.config.keys.gemini:
            self.provider = "gemini"
            if not self.model_name:
                self.model_name = "models/text-embedding-004" # Current best for Gemini
        elif self.config.keys.openai:
            self.provider = "openai"
            if not self.model_name:
                self.model_name = "text-embedding-3-small"
        else:
            self.provider = "local"
            if not self.model_name:
                self.model_name = "all-MiniLM-L6-v2"
        
        logger.info(f"Embeddings: Auto-resolved to {self.provider} ({self.model_name})")

    def encode(self, sentences: Union[str, List[str]]) -> np.ndarray:
        """
        Generates embeddings for the given text(s).
        Returns a numpy array of embeddings.
        """
        if isinstance(sentences, str):
            sentences = [sentences]
            
        if self.provider == "local":
            return self.local_model.encode(sentences)
            
        elif self.provider in ["openai", "gemini"]:
            try:
                # litellm expects model="provider/model_name" usually, or just model_name if known
                # For gemini, it's often "gemini/text-embedding-004"
                model_id = self.model_name
                if self.provider == "gemini" and not model_id.startswith("gemini/"):
                     model_id = f"gemini/{model_id}"

                # Batch size handling might be needed for large inputs, but Librarian chunks file-by-file
                response = litellm.embedding(
                    model=model_id,
                    input=sentences
                )
                
                # Extract embeddings
                # response.data is a list of objects with .embedding
                embeddings = [d['embedding'] for d in response['data']]
                return np.array(embeddings)
                
            except Exception as e:
                logger.error(f"Embeddings: Failed to get embeddings from {self.provider}: {e}")
                # Fallback to local? 
                # If we configured remote, we probably want to fail noisy, or fallback?
                # For now fail noisy so user knows config is wrong.
                raise
        
        else:
            raise ValueError(f"Unknown embedding provider: {self.provider}")
