import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from auric.interface.adapters.base import BasePact, PactEvent

logger = logging.getLogger("auric.pact.telegram")

class TelegramPact(BasePact):
    """Telegram adapter for the Auric Pact system."""
    
    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.application: Application | None = None
        self._started = False
        self._typing_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Initialize and start the Telegram bot."""
        if self._started:
            return

        logger.info("Initializing Telegram Pact...")
        self.application = Application.builder().token(self.token).build()

        # Register message handler for text content
        self.application.add_handler(MessageHandler(filters.TEXT, self._telegram_handle_message))

        await self.application.initialize()
        await self.application.start()
        
        # Start background polling
        if self.application.updater:
            await self.application.updater.start_polling(drop_pending_updates=False)
        
        self._started = True
        logger.info("Telegram Pact started.")

    async def stop(self) -> None:
        """Stop the Telegram bot and cleanup resources."""
        if not self._started or not self.application:
            return

        logger.info("Stopping Telegram Pact...")
        if self.application.updater:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        
        # Cleanup typing tasks
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        
        self._started = False
        logger.info("Telegram Pact stopped.")

    async def send_message(self, target_id: str, content: str) -> None:
        """Send a message to a specific chat ID."""
        await self.stop_typing(target_id)
        
        if not self._started or not self.application:
            logger.error("Cannot send message: Telegram Pact not started.")
            return

        try:
            await self.application.bot.send_message(chat_id=target_id, text=content)
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message to {target_id}: {e}")

    async def trigger_typing(self, target_id: str) -> None:
        """Trigger a persistent typing indicator on the target chat."""
        if not self._started or not self.application:
            return

        if target_id in self._typing_tasks and not self._typing_tasks[target_id].done():
            return

        async def _typing_loop(chat_id: str):
            try:
                while True:
                    await self.application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                    await asyncio.sleep(4) # Action expires after ~5s
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in Telegram typing loop for {chat_id}: {e}")

        self._typing_tasks[target_id] = asyncio.create_task(_typing_loop(target_id))

    async def stop_typing(self, target_id: str) -> None:
        """Stop the persistent typing indicator for a target."""
        if task := self._typing_tasks.pop(target_id, None):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _telegram_handle_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """Internal handler for incoming Telegram messages."""
        if not update.message or not update.message.text:
            return

        user = update.message.from_user
        event = PactEvent(
            platform="telegram",
            sender_id=str(update.message.chat_id),
            content=update.message.text,
            timestamp=update.message.date,
            metadata={
                "username": user.username if user else None,
                "first_name": user.first_name if user else None,
                "message_id": update.message.message_id
            }
        )
        
        if update.message.reply_to_message:
            event.reply_to_id = str(update.message.reply_to_message.message_id)

        await self._emit(event)
