import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import asyncio
from auric.brain.llm_gateway import LLMGateway
from auric.core.config import AuricConfig, AgentsConfig, ModelConfig, LLMKeys
from auric.core.database import AuditLogger, LLMInteraction

@pytest.fixture
def mock_config():
    config = Mock(spec=AuricConfig)
    config.agents = Mock(spec=AgentsConfig)
    config.agents.is_local = False
    config.agents.models = {
        "smart_model": Mock(spec=ModelConfig, provider="openai", model="gpt-4", enabled=True),
        "fast_model": Mock(spec=ModelConfig, provider="gemini", model="gemini-pro", enabled=True),
        "disabled": Mock(spec=ModelConfig, provider="openai", model="gpt-3.5", enabled=False)
    }
    
    config.keys = Mock(spec=LLMKeys)
    config.keys.model_dump.return_value = {
        "openai": "sk-openai-key",
        "gemini": "sk-gemini-key",
        "anthropic": "sk-anthropic-key"
    }
    config.keys.openai = "sk-openai-key"
    config.keys.gemini = "sk-gemini-key"
    config.keys.anthropic = "sk-anthropic-key"
    config.keys.openrouter = "sk-openrouter-key"
    
    return config

@pytest.fixture
def mock_audit_logger():
    return AsyncMock(spec=AuditLogger)

class TestLLMGatewayInitialization:
    def test_init(self, mock_config, mock_audit_logger):
        gateway = LLMGateway(mock_config, mock_audit_logger)
        assert gateway.config == mock_config
        assert gateway.audit_logger == mock_audit_logger
        assert gateway.models_config == mock_config.agents.models
        assert isinstance(gateway._local_semaphore, asyncio.Semaphore)

    def test_is_local_model_prefixes(self, mock_config):
        gateway = LLMGateway(mock_config)
        assert gateway.is_local_model("ollama/llama3")
        assert gateway.is_local_model("local/mistral")
        assert not gateway.is_local_model("gpt-4")
        assert not gateway.is_local_model("gemini-pro")

    def test_is_local_model_global_flag(self, mock_config):
        mock_config.agents.is_local = True
        gateway = LLMGateway(mock_config)
        assert gateway.is_local_model("gpt-4") # Should be true due to global flag

class TestLLMGatewayChat:
    @pytest.mark.asyncio
    async def test_chat_completion_success(self, mock_config, mock_audit_logger):
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            gateway = LLMGateway(mock_config, mock_audit_logger)
            
            mock_response = Mock()
            mock_response.choices = [Mock(message=Mock(content="Hello", role="assistant"))]
            mock_response._hidden_params = {"response_cost": 0.001}
            mock_complete.return_value = mock_response

            response = await gateway.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                tier="smart_model"
            )
            
            assert response == mock_response
            # Verify correct model and key used (smart -> openai/gpt-4)
            mock_complete.assert_called_with(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hi"}],
                api_key="sk-openai-key",
                num_retries=3
            )
            # Verify audit logging
            mock_audit_logger.log_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_completion_local_semaphore(self, mock_config, mock_audit_logger):
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            gateway = LLMGateway(mock_config, mock_audit_logger)
            mock_complete.return_value = Mock(choices=[Mock(message=Mock(content="Hi"))])
            
            # Retrieve semaphore to spy on it
            sem_spy = MagicMock(wraps=gateway._local_semaphore)
            gateway._local_semaphore = sem_spy
            
            #  Force local model via prefix
            mock_config.agents.models["local_tier"] = Mock(spec=ModelConfig, provider="ollama", model="ollama/llama3", enabled=True)
            
            await gateway.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                tier="local_tier"
            )
            
            # Verify semaphore was acquired
            assert sem_spy.__aenter__.called

    @pytest.mark.asyncio
    async def test_chat_completion_disabled_model(self, mock_config):
        gateway = LLMGateway(mock_config)
        with pytest.raises(ValueError) as exc:
            await gateway.chat_completion(messages=[], tier="disabled")
        assert "disabled" in str(exc.value)

    @pytest.mark.asyncio
    async def test_chat_completion_missing_tier(self, mock_config):
        gateway = LLMGateway(mock_config)
        # Should fallback to smart_model (gpt-4) if tier is missing
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            mock_complete.return_value = Mock(choices=[Mock(message=Mock(content="OK"))])
            
            await gateway.chat_completion(messages=[], tier="non_existent")
            
            # Verify it fell back to gpt-4
            assert mock_complete.call_args[1]["model"] == "gpt-4"
            
    @pytest.mark.asyncio
    async def test_chat_completion_key_fallback(self, mock_config):
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            gateway = LLMGateway(mock_config)
            mock_complete.return_value = Mock(choices=[Mock(message=Mock(content="Hi"))])
            
            # Setup a model where provider != key name directly (e.g. "openai_chat" -> "openai")
            # And modify mock_config keys to only have "openai"
            mock_config.agents.models["custom"] = Mock(spec=ModelConfig, provider="openai_custom", model="gpt-4-custom", enabled=True)
            
            await gateway.chat_completion(messages=[], tier="custom")
            
            # It should fallback to heuristic check for "openai" key
            args, kwargs = mock_complete.call_args
            assert kwargs["api_key"] == "sk-openai-key"

    @pytest.mark.asyncio
    async def test_malformed_response_recovery(self, mock_config):
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            gateway = LLMGateway(mock_config)
            
            # Simulate exception with embedded content
            error_msg = "Error: MALFORMED_FUNCTION_CALL ... 'content': 'Recovered Text' ..."
            mock_complete.side_effect = Exception(error_msg)
            
            response = await gateway.chat_completion(messages=[], tier="smart_model")
            
            assert response.choices[0].message.content == "Recovered Text"


class TestLLMGatewayAudit:
    @pytest.mark.asyncio
    async def test_audit_logging_structure(self, mock_config, mock_audit_logger):
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            gateway = LLMGateway(mock_config, mock_audit_logger)
            
            # Mock successful response with usage stats
            mock_response = Mock()
            mock_response.choices = [Mock(message=Mock(content="Response Content"))]
            mock_response.usage = Mock(prompt_tokens=10, completion_tokens=20)
            mock_response._hidden_params = {"response_cost": 0.05}
            mock_complete.return_value = mock_response

            await gateway.chat_completion(
                messages=[{"role": "user", "content": "Input"}],
                tier="smart_model"
            )
            
            assert mock_audit_logger.log_llm.called
            interaction = mock_audit_logger.log_llm.call_args[0][0]
            
            assert isinstance(interaction, LLMInteraction)
            assert interaction.model == "gpt-4"
            assert interaction.input_messages == [{"role": "user", "content": "Input"}]
            assert interaction.output_content == "Response Content"
            assert interaction.prompt_tokens == 10
            assert interaction.completion_tokens == 20
            # hidden_params is mocked to return 0.05
            assert interaction.total_cost == 0.05

    @pytest.mark.asyncio
    async def test_audit_logging_serialization(self, mock_config, mock_audit_logger):
        """Verify that objects (like Pydantic models) in messages are serialized."""
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            gateway = LLMGateway(mock_config, mock_audit_logger)
            mock_complete.return_value = Mock(choices=[Mock(message=Mock(content="OK"))])
            
            # Mock an object with model_dump (like a Pydantic model)
            obj_msg = Mock()
            obj_msg.model_dump.return_value = {"role": "system", "content": "System Prompt"}
            
            await gateway.chat_completion(
                messages=[obj_msg, {"role": "user", "content": "Hi"}],
                tier="smart_model"
            )
            
            interaction = mock_audit_logger.log_llm.call_args[0][0]
            # First message should be the dict from model_dump
            assert interaction.input_messages[0] == {"role": "system", "content": "System Prompt"}

    @pytest.mark.asyncio
    async def test_audit_logging_failure_safe(self, mock_config, mock_audit_logger):
        """Verify that logging failure does NOT break the main chat execution."""
        with patch("auric.brain.llm_gateway.litellm.acompletion", new_callable=AsyncMock) as mock_complete:
            gateway = LLMGateway(mock_config, mock_audit_logger)
            mock_complete.return_value = Mock(choices=[Mock(message=Mock(content="OK"))])
            
            # Make logger raise exception
            mock_audit_logger.log_llm.side_effect = Exception("DB Error")
            
            # Should NOT raise exception
            await gateway.chat_completion(messages=[], tier="smart_model")
            
            # And we should have logged the error (check system logger if possible, or just ensure we reached here)
