import asyncio
import logging
from typing import Optional

import discord
from auric.interface.adapters.base import BasePact, PactEvent

logger = logging.getLogger("auric.pact.discord")

class AuricDiscordClient(discord.Client):
    """
    Internal Discord Client to handle events.
    """
    def __init__(self, pact: 'DiscordPact', *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pact = pact

    async def on_ready(self):
        logger.info(f"Discord connected as {self.user} (ID: {self.user.id})")

    async def on_message(self, message: discord.Message):
        # Ignore own messages
        if message.author == self.user:
            return

        # Normalize to PactEvent
        event = PactEvent(
            platform="discord",
            sender_id=str(message.author.id),
            content=message.content,
            timestamp=message.created_at,
            metadata={
                "channel_id": str(message.channel.id),
                "author_name": message.author.name,
                "author_display": message.author.display_name,
                "is_dm": isinstance(message.channel, discord.DMChannel)
            }
        )
        
        if message.reference and message.reference.message_id:
             event.reply_to_id = str(message.reference.message_id)

        # Emit via the parent Pact
        await self.pact._emit(event)


class DiscordPact(BasePact):
    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.client: Optional[AuricDiscordClient] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """
        Start the Discord client in a background task.
        """
        if self.client:
            return

        logger.info("Initializing Discord Pact...")
        
        # Intents
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True # Critical for reading content
        intents.dm_messages = True

        self.client = AuricDiscordClient(pact=self, intents=intents)

        # Start the client in a background task because client.start() is blocking (run until stopped)
        self._task = asyncio.create_task(self._run_client())
        logger.info("Discord Pact background task started.")

    async def _run_client(self):
        try:
            if self.client:
                await self.client.start(self.token)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Discord Client crashed: {e}")

    async def stop(self) -> None:
        """
        Stop the Discord client.
        """
        if not self.client:
            return

        logger.info("Stopping Discord Pact...")
        await self.client.close()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.client = None
        logger.info("Discord Pact stopped.")

    async def send_message(self, target_id: str, content: str) -> None:
        """
        Send a message. target_id could be a channel_id or user_id.
        For simplicity, we assume target_id is a channel_id (which is standard for bot replies).
        If we want to DM, we'd need to fetch user.
        """
        if not self.client or not self.client.is_ready():
            logger.error("Cannot send message: Discord Pact not ready.")
            return

        try:
            # Try to interpret target_id as channel integer
            try:
                channel_id = int(target_id)
                channel = self.client.get_channel(channel_id)
                if channel:
                    if hasattr(channel, 'send'):
                        await channel.send(content)
                    else:
                        logger.warning(f"Target channel {target_id} is not sendable.")
                else:
                    # If channel not found in cache, might fetch or it's a DM user id?
                    # For now, simplistic approach: only cache.
                    user = await self.client.fetch_user(channel_id)
                    if user:
                        await user.send(content)
                    else:
                        logger.error(f"Target {target_id} not found.")
            except ValueError:
                logger.error(f"Invalid target ID format: {target_id}")

        except Exception as e:
            logger.error(f"Failed to send Discord message to {target_id}: {e}")
