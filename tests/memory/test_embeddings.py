"""
Unit tests for auric.memory.embeddings.EmbeddingWrapper.

Tests cover:
- __init__ with agents.models embeddings_model config (enabled, disabled, google→gemini alias)
- __init__ with fallback to root embeddings config
- __init__ with local provider (default model, custom model, load failure)
- __init__ with API key injection into os.environ
- _resolve_auto_provider: gemini key, openai key, no keys (local fallback),
  custom model preserved, no model defaults
- encode: single string normalised to list, local provider delegation,
  openai provider via litellm, gemini provider with model-id prefix,
  gemini provider already prefixed, litellm error propagation,
  unknown provider raises ValueError
"""

import os
import pytest
import numpy as np
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    *,
    embeddings_provider="auto",
    embeddings_model=None,
    gemini_key=None,
    openai_key=None,
    agent_embeddings_enabled=None,
    agent_embeddings_provider=None,
    agent_embeddings_model=None,
):
    """
    Build a minimal mock AuricConfig with the fields that EmbeddingWrapper
    actually reads.
    """
    config = MagicMock()

    # --- keys ---
    config.keys.gemini = gemini_key
    config.keys.openai = openai_key

    # --- root embeddings section ---
    config.embeddings.provider = embeddings_provider
    config.embeddings.model = embeddings_model

    # --- agents.models.embeddings_model section ---
    if agent_embeddings_enabled is not None:
        model_cfg = MagicMock()
        model_cfg.enabled = agent_embeddings_enabled
        model_cfg.provider = agent_embeddings_provider or "auto"
        model_cfg.model = agent_embeddings_model or ""
        config.agents.models.get.return_value = model_cfg
    else:
        config.agents.models.get.return_value = None

    return config


def _build_wrapper(config, *, patch_st=True, st_return=None):
    """
    Instantiate an EmbeddingWrapper while patching SentenceTransformer so
    that no real model download occurs.  Returns (wrapper, mock_ST_class).
    """
    with patch("auric.memory.embeddings.SentenceTransformer") as MockST:
        if st_return is not None:
            MockST.return_value = st_return
        else:
            MockST.return_value = MagicMock()

        from auric.memory.embeddings import EmbeddingWrapper
        wrapper = EmbeddingWrapper(config)

    return wrapper, MockST


# ===========================================================================
# Tests: __init__ — Agent-level model config
# ===========================================================================

class TestInitAgentModelConfig:
    """When agents.models.embeddings_model is present and enabled, it takes
    priority over the root embeddings config."""

    def test_uses_agent_model_when_enabled(self):
        cfg = _make_config(
            agent_embeddings_enabled=True,
            agent_embeddings_provider="openai",
            agent_embeddings_model="text-embedding-3-large",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "openai"
        assert wrapper.model_name == "text-embedding-3-large"

    def test_google_provider_alias_to_gemini(self):
        """'google' in config should be normalised to 'gemini'."""
        cfg = _make_config(
            agent_embeddings_enabled=True,
            agent_embeddings_provider="Google",
            agent_embeddings_model="models/text-embedding-004",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "gemini"

    def test_falls_back_to_root_when_agent_config_disabled(self):
        cfg = _make_config(
            agent_embeddings_enabled=False,
            agent_embeddings_provider="openai",
            agent_embeddings_model="should-be-ignored",
            embeddings_provider="local",
            embeddings_model="my-local-model",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "local"
        assert wrapper.model_name == "my-local-model"

    def test_falls_back_to_root_when_agent_config_absent(self):
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "openai"
        assert wrapper.model_name == "text-embedding-3-small"


# ===========================================================================
# Tests: __init__ — Local provider
# ===========================================================================

class TestInitLocalProvider:

    def test_local_loads_sentence_transformer(self):
        cfg = _make_config(embeddings_provider="local", embeddings_model="all-MiniLM-L6-v2")
        wrapper, MockST = _build_wrapper(cfg)

        assert wrapper.provider == "local"
        assert wrapper.local_model is not None
        MockST.assert_called_once_with("all-MiniLM-L6-v2")

    def test_local_default_model_when_none(self):
        cfg = _make_config(embeddings_provider="local", embeddings_model=None)
        wrapper, MockST = _build_wrapper(cfg)

        assert wrapper.model_name == "all-MiniLM-L6-v2"
        MockST.assert_called_once_with("all-MiniLM-L6-v2")

    def test_local_default_model_when_empty(self):
        cfg = _make_config(embeddings_provider="local", embeddings_model="")
        wrapper, MockST = _build_wrapper(cfg)

        assert wrapper.model_name == "all-MiniLM-L6-v2"
        MockST.assert_called_once_with("all-MiniLM-L6-v2")

    def test_local_model_load_failure_raises(self):
        cfg = _make_config(embeddings_provider="local", embeddings_model="bad-model")

        with patch("auric.memory.embeddings.SentenceTransformer", side_effect=OSError("not found")):
            from auric.memory.embeddings import EmbeddingWrapper
            with pytest.raises(OSError, match="not found"):
                EmbeddingWrapper(cfg)

    def test_local_model_not_loaded_for_remote_provider(self):
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, MockST = _build_wrapper(cfg)

        assert wrapper.local_model is None
        MockST.assert_not_called()


# ===========================================================================
# Tests: __init__ — API key injection
# ===========================================================================

class TestInitKeyInjection:

    def test_gemini_key_injected(self):
        cfg = _make_config(
            embeddings_provider="gemini",
            embeddings_model="models/text-embedding-004",
            gemini_key="gk-12345",
        )
        with patch.dict(os.environ, {}, clear=False):
            wrapper, _ = _build_wrapper(cfg)
            assert os.environ.get("GEMINI_API_KEY") == "gk-12345"

    def test_openai_key_injected(self):
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-12345",
        )
        with patch.dict(os.environ, {}, clear=False):
            wrapper, _ = _build_wrapper(cfg)
            assert os.environ.get("OPENAI_API_KEY") == "sk-12345"

    def test_both_keys_injected(self):
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            gemini_key="gk-abc",
            openai_key="sk-xyz",
        )
        with patch.dict(os.environ, {}, clear=False):
            wrapper, _ = _build_wrapper(cfg)
            assert os.environ.get("GEMINI_API_KEY") == "gk-abc"
            assert os.environ.get("OPENAI_API_KEY") == "sk-xyz"

    def test_no_keys_no_injection(self):
        cfg = _make_config(
            embeddings_provider="local",
            embeddings_model="all-MiniLM-L6-v2",
            gemini_key=None,
            openai_key=None,
        )
        sentinel_g = os.environ.pop("GEMINI_API_KEY", None)
        sentinel_o = os.environ.pop("OPENAI_API_KEY", None)

        try:
            with patch.dict(os.environ, {}, clear=False):
                wrapper, _ = _build_wrapper(cfg)
                # Keys should not have been set
                assert "GEMINI_API_KEY" not in os.environ
                assert "OPENAI_API_KEY" not in os.environ
        finally:
            # Restore originals if they existed
            if sentinel_g is not None:
                os.environ["GEMINI_API_KEY"] = sentinel_g
            if sentinel_o is not None:
                os.environ["OPENAI_API_KEY"] = sentinel_o


# ===========================================================================
# Tests: _resolve_auto_provider
# ===========================================================================

class TestResolveAutoProvider:

    def test_auto_resolves_to_gemini_when_gemini_key_present(self):
        cfg = _make_config(
            embeddings_provider="auto",
            embeddings_model=None,
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "gemini"
        assert wrapper.model_name == "models/text-embedding-004"

    def test_auto_resolves_to_openai_when_only_openai_key(self):
        cfg = _make_config(
            embeddings_provider="auto",
            embeddings_model=None,
            gemini_key=None,
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "openai"
        assert wrapper.model_name == "text-embedding-3-small"

    def test_auto_resolves_to_local_when_no_keys(self):
        cfg = _make_config(
            embeddings_provider="auto",
            embeddings_model=None,
            gemini_key=None,
            openai_key=None,
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "local"
        assert wrapper.model_name == "all-MiniLM-L6-v2"

    def test_auto_gemini_preserves_custom_model(self):
        cfg = _make_config(
            embeddings_provider="auto",
            embeddings_model="my-custom-embedding",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "gemini"
        assert wrapper.model_name == "my-custom-embedding"

    def test_auto_openai_preserves_custom_model(self):
        cfg = _make_config(
            embeddings_provider="auto",
            embeddings_model="text-embedding-ada-002",
            gemini_key=None,
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "openai"
        assert wrapper.model_name == "text-embedding-ada-002"

    def test_auto_local_preserves_custom_model(self):
        cfg = _make_config(
            embeddings_provider="auto",
            embeddings_model="paraphrase-MiniLM-L3-v2",
            gemini_key=None,
            openai_key=None,
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "local"
        assert wrapper.model_name == "paraphrase-MiniLM-L3-v2"

    def test_auto_prefers_gemini_over_openai(self):
        """When both keys are present, gemini wins."""
        cfg = _make_config(
            embeddings_provider="auto",
            embeddings_model=None,
            gemini_key="gk-test",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "gemini"


# ===========================================================================
# Tests: encode — local provider
# ===========================================================================

class TestEncodeLocal:

    def test_encode_single_string_normalised(self):
        """A bare string input is wrapped in a list before encoding."""
        cfg = _make_config(embeddings_provider="local", embeddings_model="test-model")
        mock_st = MagicMock()
        mock_st.encode.return_value = np.array([[0.1, 0.2, 0.3]])

        wrapper, _ = _build_wrapper(cfg, st_return=mock_st)
        result = wrapper.encode("hello")

        mock_st.encode.assert_called_once_with(["hello"])
        np.testing.assert_array_equal(result, np.array([[0.1, 0.2, 0.3]]))

    def test_encode_list_of_strings(self):
        cfg = _make_config(embeddings_provider="local", embeddings_model="test-model")
        mock_st = MagicMock()
        expected = np.random.rand(3, 4)
        mock_st.encode.return_value = expected

        wrapper, _ = _build_wrapper(cfg, st_return=mock_st)
        result = wrapper.encode(["a", "b", "c"])

        mock_st.encode.assert_called_once_with(["a", "b", "c"])
        np.testing.assert_array_equal(result, expected)

    def test_encode_empty_list(self):
        cfg = _make_config(embeddings_provider="local", embeddings_model="test-model")
        mock_st = MagicMock()
        mock_st.encode.return_value = np.array([])

        wrapper, _ = _build_wrapper(cfg, st_return=mock_st)
        result = wrapper.encode([])

        mock_st.encode.assert_called_once_with([])
        assert len(result) == 0


# ===========================================================================
# Tests: encode — OpenAI provider (via litellm)
# ===========================================================================

class TestEncodeOpenAI:

    def test_encode_openai_calls_litellm(self):
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        }

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response) as mock_emb:
            result = wrapper.encode(["hello", "world"])

            mock_emb.assert_called_once_with(
                model="text-embedding-3-small",
                input=["hello", "world"],
            )

        expected = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        np.testing.assert_array_almost_equal(result, expected)

    def test_encode_openai_single_string(self):
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {"data": [{"embedding": [1.0, 2.0]}]}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response):
            result = wrapper.encode("single sentence")

        assert result.shape == (1, 2)

    def test_encode_openai_model_not_prefixed(self):
        """OpenAI model names should NOT get a prefix."""
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {"data": [{"embedding": [1.0]}]}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response) as mock_emb:
            wrapper.encode("test")
            model_arg = mock_emb.call_args[1]["model"]
            assert not model_arg.startswith("openai/")
            assert model_arg == "text-embedding-3-small"


# ===========================================================================
# Tests: encode — Gemini provider (via litellm)
# ===========================================================================

class TestEncodeGemini:

    def test_encode_gemini_adds_prefix(self):
        """Gemini models without 'gemini/' prefix get it added automatically."""
        cfg = _make_config(
            embeddings_provider="gemini",
            embeddings_model="models/text-embedding-004",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {"data": [{"embedding": [0.5, 0.6]}]}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response) as mock_emb:
            wrapper.encode("test")
            model_arg = mock_emb.call_args[1]["model"]
            assert model_arg == "gemini/models/text-embedding-004"

    def test_encode_gemini_already_prefixed_not_doubled(self):
        """If the model name already starts with 'gemini/', it should not be doubled."""
        cfg = _make_config(
            embeddings_provider="gemini",
            embeddings_model="gemini/text-embedding-004",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {"data": [{"embedding": [0.5]}]}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response) as mock_emb:
            wrapper.encode("test")
            model_arg = mock_emb.call_args[1]["model"]
            assert model_arg == "gemini/text-embedding-004"
            assert not model_arg.startswith("gemini/gemini/")

    def test_encode_gemini_extracts_embeddings_correctly(self):
        cfg = _make_config(
            embeddings_provider="gemini",
            embeddings_model="models/text-embedding-004",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {
            "data": [
                {"embedding": [0.1, 0.2]},
                {"embedding": [0.3, 0.4]},
                {"embedding": [0.5, 0.6]},
            ]
        }

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response):
            result = wrapper.encode(["a", "b", "c"])

        assert result.shape == (3, 2)
        np.testing.assert_array_almost_equal(result[2], [0.5, 0.6])


# ===========================================================================
# Tests: encode — Error handling
# ===========================================================================

class TestEncodeErrors:

    def test_encode_litellm_error_propagates(self):
        """When litellm.embedding raises, EmbeddingWrapper.encode re-raises."""
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        with patch(
            "auric.memory.embeddings.litellm.embedding",
            side_effect=RuntimeError("API timeout"),
        ):
            with pytest.raises(RuntimeError, match="API timeout"):
                wrapper.encode("test")

    def test_encode_gemini_litellm_error_propagates(self):
        cfg = _make_config(
            embeddings_provider="gemini",
            embeddings_model="models/text-embedding-004",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        with patch(
            "auric.memory.embeddings.litellm.embedding",
            side_effect=ConnectionError("network down"),
        ):
            with pytest.raises(ConnectionError, match="network down"):
                wrapper.encode(["a", "b"])

    def test_encode_unknown_provider_raises_value_error(self):
        cfg = _make_config(
            embeddings_provider="anthropic",
            embeddings_model="some-model",
        )
        wrapper, _ = _build_wrapper(cfg)

        with pytest.raises(ValueError, match="Unknown embedding provider: anthropic"):
            wrapper.encode("test")


# ===========================================================================
# Tests: encode — Return type guarantees
# ===========================================================================

class TestEncodeReturnTypes:

    def test_local_returns_numpy_array(self):
        cfg = _make_config(embeddings_provider="local", embeddings_model="test-model")
        mock_st = MagicMock()
        mock_st.encode.return_value = np.array([[1.0, 2.0]])

        wrapper, _ = _build_wrapper(cfg, st_return=mock_st)
        result = wrapper.encode("hello")

        assert isinstance(result, np.ndarray)

    def test_openai_returns_numpy_array(self):
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {"data": [{"embedding": [1.0, 2.0]}]}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response):
            result = wrapper.encode("hello")

        assert isinstance(result, np.ndarray)

    def test_gemini_returns_numpy_array(self):
        cfg = _make_config(
            embeddings_provider="gemini",
            embeddings_model="models/text-embedding-004",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        fake_response = {"data": [{"embedding": [1.0, 2.0]}]}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response):
            result = wrapper.encode("hello")

        assert isinstance(result, np.ndarray)


# ===========================================================================
# Tests: encode — Edge-case inputs
# ===========================================================================

class TestEncodeEdgeCases:

    def test_encode_very_long_input(self):
        """Ensure long text is passed through without truncation by the wrapper."""
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        long_text = "word " * 10000  # ~50k chars
        fake_response = {"data": [{"embedding": [0.0] * 128}]}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response) as mock_emb:
            wrapper.encode(long_text)
            # Verify the full text was passed (as a list)
            passed_input = mock_emb.call_args[1]["input"]
            assert len(passed_input) == 1
            assert passed_input[0] == long_text

    def test_encode_multiple_sentences_batch(self):
        """A batch of many sentences is forwarded in one litellm call."""
        cfg = _make_config(
            embeddings_provider="openai",
            embeddings_model="text-embedding-3-small",
            openai_key="sk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        sentences = [f"sentence {i}" for i in range(50)]
        fake_data = [{"embedding": [float(i)]} for i in range(50)]
        fake_response = {"data": fake_data}

        with patch("auric.memory.embeddings.litellm.embedding", return_value=fake_response) as mock_emb:
            result = wrapper.encode(sentences)
            mock_emb.assert_called_once()
            assert result.shape == (50, 1)

    def test_encode_unicode_text(self):
        """Unicode text is handled correctly."""
        cfg = _make_config(embeddings_provider="local", embeddings_model="test-model")
        mock_st = MagicMock()
        mock_st.encode.return_value = np.array([[0.1, 0.2]])

        wrapper, _ = _build_wrapper(cfg, st_return=mock_st)
        result = wrapper.encode("こんにちは世界 🌍")

        mock_st.encode.assert_called_once_with(["こんにちは世界 🌍"])
        assert result.shape == (1, 2)


# ===========================================================================
# Tests: __init__ — Auto-resolve via agent-level config
# ===========================================================================

class TestInitAutoResolveWithAgentConfig:
    """When agent-level config sets provider to 'auto', auto-resolution
    should still run."""

    def test_agent_config_auto_resolves_to_gemini(self):
        cfg = _make_config(
            agent_embeddings_enabled=True,
            agent_embeddings_provider="auto",
            agent_embeddings_model="",
            gemini_key="gk-test",
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "gemini"
        assert wrapper.model_name == "models/text-embedding-004"

    def test_agent_config_auto_resolves_to_local(self):
        cfg = _make_config(
            agent_embeddings_enabled=True,
            agent_embeddings_provider="auto",
            agent_embeddings_model="",
            gemini_key=None,
            openai_key=None,
        )
        wrapper, _ = _build_wrapper(cfg)

        assert wrapper.provider == "local"
        assert wrapper.model_name == "all-MiniLM-L6-v2"
