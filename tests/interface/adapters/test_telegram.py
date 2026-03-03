import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from datetime import datetime
from telegram import Update, User, Message, Chat
from telegram.ext import Application, ContextTypes, MessageHandler
from telegram.constants import ChatAction

from auric.interface.adapters.telegram import TelegramPact
from auric.interface.adapters.base import PactEvent

@pytest.fixture
def telegram_pact():
    return TelegramPact(token="fake_token")

@pytest.fixture
def mock_update():
    update = MagicMock(spec=Update)
    message = MagicMock(spec=Message)
    user = MagicMock(spec=User)
    chat = MagicMock(spec=Chat)
    
    update.message = message
    message.from_user = user
    message.chat = chat
    message.chat_id = 12345
    message.text = "Hello Auric"
    message.date = datetime.now()
    message.message_id = 67890
    message.reply_to_message = None
    
    user.id = 54321
    user.username = "testuser"
    user.first_name = "Test"
    
    return update

@pytest.fixture
def mock_context():
    return MagicMock(spec=ContextTypes.DEFAULT_TYPE)

@pytest.mark.asyncio
async def test_telegram_pact_start(telegram_pact):
    mock_app = MagicMock(spec=Application)
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock()
    
    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app
    
    with patch("telegram.ext.Application.builder", return_value=mock_builder):
        await telegram_pact.start()
        
        assert telegram_pact._started is True
        mock_builder.token.assert_called_once_with("fake_token")
        mock_builder.build.assert_called_once()
        mock_app.add_handler.assert_called_once()
        assert isinstance(mock_app.add_handler.call_args[0][0], MessageHandler)
        mock_app.initialize.assert_called_once()
        mock_app.start.assert_called_once()
        mock_app.updater.start_polling.assert_called_once_with(drop_pending_updates=False)

@pytest.mark.asyncio
async def test_telegram_pact_start_already_started(telegram_pact):
    telegram_pact._started = True
    with patch("telegram.ext.Application.builder") as mock_builder:
        await telegram_pact.start()
        mock_builder.assert_not_called()

@pytest.mark.asyncio
async def test_telegram_pact_stop(telegram_pact):
    mock_app = MagicMock(spec=Application)
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.stop = AsyncMock()
    
    telegram_pact.application = mock_app
    telegram_pact._started = True
    
    await telegram_pact.stop()
    
    assert telegram_pact._started is False
    mock_app.updater.stop.assert_called_once()
    mock_app.stop.assert_called_once()
    mock_app.shutdown.assert_called_once()

@pytest.mark.asyncio
async def test_telegram_pact_stop_not_started(telegram_pact):
    await telegram_pact.stop()
    # Should not raise error or call anything

@pytest.mark.asyncio
async def test_telegram_pact_send_message_success(telegram_pact):
    mock_app = MagicMock(spec=Application)
    mock_app.bot.send_message = AsyncMock()
    telegram_pact.application = mock_app
    telegram_pact._started = True
    
    await telegram_pact.send_message("12345", "Test message")
    
    mock_app.bot.send_message.assert_called_once_with(chat_id="12345", text="Test message")

@pytest.mark.asyncio
async def test_telegram_pact_send_message_not_started(telegram_pact, caplog):
    await telegram_pact.send_message("12345", "Test message")
    assert "Cannot send message: Telegram Pact not started." in caplog.text

@pytest.mark.asyncio
async def test_telegram_pact_trigger_typing(telegram_pact):
    mock_app = MagicMock()
    # Create an event to signal when the mock is called
    call_event = asyncio.Event()
    
    async def mock_send_chat_action(*args, **kwargs):
        call_event.set()
        return MagicMock()

    mock_app.bot.send_chat_action = AsyncMock(side_effect=mock_send_chat_action)
    telegram_pact.application = mock_app
    telegram_pact._started = True
    
    # We patch asyncio.sleep in the module where it's used.
    # We must ensure it yields, otherwise the 'while True' loop spins infinitely.
    async def mock_sleep_yield(delay):
        await asyncio.sleep(0.01) # Real short sleep to yield control

    with patch("auric.interface.adapters.telegram.asyncio.sleep", side_effect=mock_sleep_yield) as mock_sleep:
        await telegram_pact.trigger_typing("12345")
        
        # Wait for the event with a timeout
        try:
            await asyncio.wait_for(call_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail("send_chat_action was not called within timeout")
            
        assert "12345" in telegram_pact._typing_tasks
        mock_app.bot.send_chat_action.assert_called_with(chat_id="12345", action=ChatAction.TYPING)
        
        await telegram_pact.stop_typing("12345")
        assert "12345" not in telegram_pact._typing_tasks

@pytest.mark.asyncio
async def test_telegram_handle_message(telegram_pact, mock_update, mock_context):
    received_event = None
    async def handler(event):
        nonlocal received_event
        received_event = event
    
    telegram_pact.on_message(handler)
    
    await telegram_pact._telegram_handle_message(mock_update, mock_context)
    
    assert received_event is not None
    assert received_event.platform == "telegram"
    assert received_event.sender_id == "12345"
    assert received_event.content == "Hello Auric"
    assert received_event.metadata["username"] == "testuser"
    assert received_event.metadata["message_id"] == 67890

@pytest.mark.asyncio
async def test_telegram_handle_message_reply(telegram_pact, mock_update, mock_context):
    received_event = None
    async def handler(event):
        nonlocal received_event
        received_event = event
    
    telegram_pact.on_message(handler)
    
    reply_msg = MagicMock(spec=Message)
    reply_msg.message_id = 11111
    mock_update.message.reply_to_message = reply_msg
    
    await telegram_pact._telegram_handle_message(mock_update, mock_context)
    
    assert received_event is not None
    assert received_event.reply_to_id == "11111"

@pytest.mark.asyncio
async def test_telegram_handle_message_no_text(telegram_pact, mock_update, mock_context):
    received_event = None
    async def handler(event):
        nonlocal received_event
        received_event = event
    
    telegram_pact.on_message(handler)
    mock_update.message.text = None
    
    await telegram_pact._telegram_handle_message(mock_update, mock_context)
    
    assert received_event is None
