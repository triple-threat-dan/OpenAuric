import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import TelegramError

from auric.interface.adapters.base import BasePact, PactEvent

logger = logging.getLogger("auric.pact.telegram")

class TelegramPact(BasePact):
    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.application: Optional[Application] = None
        self._started = False
        self._typing_tasks: Dict[str, asyncio.Task] = {} # chat_id -> task

    async def start(self) -> None:
        """
        Initialize and start the Telegram bot application.
        """
        if self._started:
            return

        logger.info("Initializing Telegram Pact...")
        
        # Build the application
        builder = Application.builder().token(self.token)
        self.application = builder.build()

        # Register handlers
        # We want to capture text messages. 
        # filters.TEXT & ~filters.COMMAND captures non-command text.
        # We might want commands too, but usually an agent just listens to chat. 
        # Let's catch everything that is text.
        text_handler = MessageHandler(filters.TEXT, self._telegram_handle_message)
        self.application.add_handler(text_handler)

        # Initialize and Start
        await self.application.initialize()
        await self.application.start()
        
        # Start Polling (non-blocking way for existing loop)
        # We use start_polling() but we need to manage the updater's lifecycle carefully 
        # if we are in a shared loop. 
        # application.updater.start_polling() is asynchronous but returns a coroutine?
        # Actually in v20+, start_polling() starts the background task.
        
        await self.application.updater.start_polling(drop_pending_updates=False) # type: ignore
        
        self._started = True
        logger.info("Telegram Pact started.")

    async def stop(self) -> None:
        """
        Stop the Telegram bot.
        """
        if not self._started or not self.application:
            return

        logger.info("Stopping Telegram Pact...")
        await self.application.updater.stop() # type: ignore
        await self.application.stop()
        await self.application.shutdown()
        self._started = False
        logger.info("Telegram Pact stopped.")

    async def send_message(self, target_id: str, content: str) -> None:
        """
        Send a message to a chat_id.
        """
        # Stop typing indicator first
        await self.stop_typing(target_id)
        
        if not self._started or not self.application:
            logger.error("Cannot send message: Telegram Pact not started.")
            return

        try:
            await self.application.bot.send_message(chat_id=target_id, text=content)
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message to {target_id}: {e}")

    async def trigger_typing(self, target_id: str) -> None:
        """
        Trigger a typing indicator on the target chat.
        """
        if not self._started or not self.application:
            return

        # If already typing for this target, don't spawn another task
        if target_id in self._typing_tasks and not self._typing_tasks[target_id].done():
            return

        async def _typing_loop(chat_id: str):
            from telegram.constants import ChatAction
            try:
                while True:
                    # Telegram typing action expires after ~5 seconds
                    await self.application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                    await asyncio.sleep(4)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in Telegram typing loop for {chat_id}: {e}")

        # Spawn loop
        self._typing_tasks[target_id] = asyncio.create_task(_typing_loop(target_id))

    async def stop_typing(self, target_id: str) -> None:
        """
        Stop the persistent typing indicator for a target.
        """
        if target_id in self._typing_tasks:
            task = self._typing_tasks[target_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self._typing_tasks[target_id]

    async def _telegram_handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Internal handler for Telegram updates.
        """
        if not update.message or not update.message.text:
            return

        # Normalize to PactEvent
        user = update.message.from_user
        
        event = PactEvent(
            platform="telegram",
            sender_id=str(update.message.chat_id), # Use chat_id as the primary identifier for replies
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

        # Emit to PactManager
        await self._emit(event)
