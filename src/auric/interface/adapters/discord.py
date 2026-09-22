import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from auric.interface.adapters.base import BasePact, PactEvent

logger = logging.getLogger("auric.pact.discord")

class AuricDiscordClient(discord.Client):
    """Internal Discord Client to handle events."""
    
    def __init__(self, pact: 'DiscordPact', *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pact = pact

    async def on_ready(self):
        logger.info(f"Discord connected as {self.user} (ID: {self.user.id})")
        if self.intents.members:
            for guild in self.guilds:
                await guild.chunk()
                logger.info(f"Chunked guild {guild.name} ({guild.member_count} members)")

    async def _is_bot_loop(self, channel, limit: int = 4) -> bool:
        """Check if the last `limit` messages are all from bots."""
        try:
            async for msg in channel.history(limit=limit):
                if not msg.author.bot:
                    return False
            return True
        except Exception as e:
            logger.error(f"Failed to check bot loop: {e}")
            return False

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        should_respond = False
        is_dm = isinstance(message.channel, discord.DMChannel)
        
        if is_dm or self.user in message.mentions:
            should_respond = True
        elif re.match(rf"^{re.escape(self.pact.agent_name)}\b\s*[,\:]?\s*", message.content.strip(), re.IGNORECASE):
            should_respond = True
        elif message.reference:
            ref = message.reference.cached_message
            if not ref:
                try:
                    ref = await message.channel.fetch_message(message.reference.message_id)
                except Exception:
                    pass
            if ref and ref.author == self.user:
                should_respond = True

        if not should_respond:
            return

        if message.author.bot:
             if await self._is_bot_loop(message.channel, limit=self.pact.bot_loop_limit):
                 logger.warning(f"Bot Loop Detected in {message.channel}. Stopping response to {message.author.name}.")
                 return

        from auric.core.pairing import PairingManager
        pairing_mgr = PairingManager()
        user_id = str(message.author.id)
        
        if not pairing_mgr.is_user_allowed("discord", user_id, self.pact.allowed_users):
            logger.warning(f"Unauthorized message from {message.author.name} ({user_id})")
            code = pairing_mgr.create_request("discord", user_id, message.author.name)
            
            should_warn = is_dm or self.user in message.mentions or \
                          re.search(rf"\b{re.escape(self.pact.agent_name)}\b", message.content, re.IGNORECASE)
                
            if should_warn:
                try:
                    await message.channel.send(
                        f"⛔ **Unauthorized Identity**\n"
                        f"You are not authorized to interact with me.\n"
                        f"Please ask the administrator to approve this pairing code:\n"
                        f"# `{code}`"
                    )
                except Exception as e:
                     logger.error(f"Failed to send auth warning: {e}")
            return

        if self.pact.allowed_channels and str(message.channel.id) not in self.pact.allowed_channels:
             if not is_dm:
                 return

        if message.content.strip() == "/new":
             if not self.pact.allowed_users or user_id not in self.pact.allowed_users:
                 await message.channel.send("⛔ You are not authorized to reset the session.")
                 return
             await self.pact.trigger_new_session(str(message.channel.id))
             return

        clean_content = message.content
        for user in message.mentions:
            display_name = getattr(user, "display_name", user.name)
            clean_content = re.sub(f"<@!?{user.id}>", f"@{display_name}", clean_content)
        
        for channel in message.channel_mentions:
             clean_content = clean_content.replace(f"<#{channel.id}>", f"#{channel.name}")

        event = PactEvent(
            platform="discord",
            sender_id=str(message.channel.id),
            content=clean_content,
            timestamp=message.created_at,
            metadata={
                "channel_id": str(message.channel.id),
                "channel_name": getattr(message.channel, "name", "DM"),
                "guild_name": getattr(message.guild, "name", "Direct Message") if message.guild else "Direct Message",
                "author_id": user_id,
                "author_name": message.author.name,
                "author_display": message.author.display_name,
                "is_dm": is_dm
            }
        )
        
        if message.reference and message.reference.message_id:
             event.reply_to_id = str(message.reference.message_id)

        await self.pact._emit(event)


class DiscordPact(BasePact):
    def __init__(self, token: str, allowed_channels: List[str] = None, allowed_users: List[str] = None, 
                 agent_name: str = "Auric", api_port: int = 8000, bot_loop_limit: int = 4):
        super().__init__()
        self.token = token
        self.allowed_channels = allowed_channels or []
        self.allowed_users = allowed_users or []
        self.agent_name = agent_name
        self.api_port = api_port
        self.bot_loop_limit = bot_loop_limit
        self.client: Optional[AuricDiscordClient] = None
        self._task: Optional[asyncio.Task] = None
        self._typing_tasks: Dict[str, asyncio.Task] = {}

    async def trigger_new_session(self, target_id: str) -> None:
        """Triggers a new session via the local API."""
        import aiohttp
        try:
            url = f"http://127.0.0.1:{self.api_port}/api/sessions/new"
            payload = {"context": f"discord:{target_id}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        new_sid = data.get("session_id")
                        await self.send_message(target_id, f"🔄 **Session Reset**. New ID: `{new_sid}`")
                    else:
                        await self.send_message(target_id, f"⚠️ Failed to reset session. API Status: {resp.status}")
        except Exception as e:
            logger.error(f"Failed to trigger new session: {e}")
            await self.send_message(target_id, f"⚠️ Error triggering new session: {e}")

    async def start(self) -> None:
        if self.client:
            return

        logger.info("Initializing Discord Pact...")
        intents = discord.Intents.default()
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.dm_messages = True

        self.client = AuricDiscordClient(pact=self, intents=intents)
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

    async def send_dm(self, user_id: str, content: str) -> Optional[str]:
        """Send a Direct Message to a user."""
        await self.stop_typing(user_id)
        
        if not self.client or not self.client.is_ready():
            return None # Expected by tests when not ready

        try:
            user = None
            if user_id.isdigit():
                try:
                    user = await self.client.fetch_user(int(user_id))
                except Exception:
                    pass
            
            if not user:
                user = next((u for u in self.client.users if u != self.client.user and 
                            (u.name.lower() == user_id.lower() or getattr(u, "global_name", "").lower() == user_id.lower())), None)
                
                if not user:
                    for guild in self.client.guilds:
                        user = discord.utils.get(guild.members, id=int(user_id)) if user_id.isdigit() else None
                        if not user:
                             user = discord.utils.get(guild.members, name=user_id) or \
                                    discord.utils.get(guild.members, display_name=user_id)
                        
                        if not user: # CI match fallback
                             user = next((m for m in guild.members if m.name.lower() == user_id.lower() and m != self.client.user), None)
                        if not user: # Display name CI match
                             user = next((m for m in guild.members if m.display_name.lower() == user_id.lower() and m != self.client.user), None)
                        
                        if user and user != self.client.user:
                            break

            if user:
                for chunk in self._chunk_message(content):
                    await user.send(chunk)
                return f"Message sent to {user.name} ({user.id})"
            
            debug_info = []
            for g in self.client.guilds:
                members = [f"{m.name} ({getattr(m, 'display_name', m.name)})" for m in g.members]
                debug_info.append(f"Guild {getattr(g, 'name', 'Unknown')}: {members[:5]}... (Total {len(members)})")
            
            return f"User '{user_id}' not found. Visibile Context: {'; '.join(debug_info)}"
        except Exception as e:
            logger.error(f"Failed to send DM to {user_id}: {e}")
            return f"Error sending DM: {e}"

    async def lookup_user(self, query: str) -> str:
        """Search for a user by name or ID."""
        if not self.client or not self.client.is_ready():
            return "Error: Discord Pact not ready."

        matches = []
        unique_ids = set()
        query_lower = query.lower()
        
        if query.isdigit():
            try:
                user = await self.client.fetch_user(int(query))
                if user and user != self.client.user:
                    unique_ids.add(user.id)
                    matches.append(f"- {user.name} (Display: {user.display_name}) [ID: {user.id}] - Found in: Direct Fetch")
            except Exception:
                pass

        for guild in self.client.guilds:
            for member in guild.members:
                if member.id not in unique_ids and member != self.client.user:
                    if query_lower in member.name.lower() or query_lower in member.display_name.lower():
                        unique_ids.add(member.id)
                        matches.append(f"- {member.name} (Display: {member.display_name}) [ID: {member.id}] - Found in: Guild '{guild.name}'")
        
        if not matches:
            return f"No users found matching '{query}'. Context: {len(self.client.guilds)} guilds scaned."
        
        return "Found users:\n" + "\n".join(matches[:10])

    @staticmethod
    def _chunk_message(content: str, max_length: int = 2000) -> List[str]:
        # Fix "naked" emojis (e.g. a:name:id or name:id) that are missing brackets
        # Matches patterns like a:cathi:1478099851047862436 or cathi:1478099851047862436
        # only if they aren't already inside brackets.
        def fix_emojis(text: str) -> str:
            # Matches a:name:id or :name:id only if NOT already wrapped in < >
            # We look for the start of the pattern.
            # We use a pattern that ensures we match the FULL emoji string.
            pattern = r"(?<!<)(?<!<a)(?<!:)(a?:\w+:\d+)(?!>)"
            return re.sub(pattern, r"<\1>", text)

        content = fix_emojis(content)

        if len(content) <= max_length:
            return [content]
        
        chunks = []
        remaining = content
        while remaining:
            if len(remaining) <= max_length:
                chunks.append(remaining)
                break
            
            split_idx = -1
            for separator in ('\n\n', '\n', ' '):
                idx = remaining.rfind(separator, 0, max_length)
                if idx != -1 and idx >= max_length // 2:
                    split_idx = idx
                    break
            
            if split_idx == -1:
                split_idx = max_length
            
            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip()
        
        return [c for c in chunks if c]

    async def send_channel_message(self, channel_id: str, content: str) -> None:
        await self.stop_typing(channel_id)
        if not self.client or not self.client.is_ready():
            return

        try:
            c_id = int(channel_id)
            channel = self.client.get_channel(c_id) or await self.client.fetch_channel(c_id)
            if channel and hasattr(channel, 'send'):
                for chunk in self._chunk_message(content):
                    await channel.send(chunk)
        except Exception as e:
             logger.error(f"Failed to send message to channel {channel_id}: {e}")

    async def send_message(self, target_id: str, content: str) -> None:
        try:
            await self.send_channel_message(target_id, content)
        except Exception:
            await self.send_dm(target_id, content)

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        if not self.client or not self.client.is_ready():
            return
        try:
            c_id, m_id = int(channel_id), int(message_id)
            channel = self.client.get_channel(c_id) or await self.client.fetch_channel(c_id)
            if channel:
                message = await channel.fetch_message(m_id)
                if message:
                    await message.add_reaction(emoji)
        except Exception as e:
            logger.error(f"Failed to add reaction: {e}")

    async def trigger_typing(self, target_id: str) -> None:
        if not self.client or not self.client.is_ready():
            return

        if target_id in self._typing_tasks and not self._typing_tasks[target_id].done():
            return

        async def _typing_loop(t_id_str: str):
            try:
                t_id = int(t_id_str)
                target = self.client.get_channel(t_id) or self.client.get_user(t_id)
                if not target:
                    try:
                        target = await self.client.fetch_channel(t_id)
                    except Exception:
                        try:
                            target = await self.client.fetch_user(t_id)
                        except Exception:
                            pass
                
                if target and hasattr(target, 'typing'):
                    async with target.typing():
                        while True:
                            await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in typing loop for {t_id_str}: {e}")

        self._typing_tasks[target_id] = asyncio.create_task(_typing_loop(target_id))

    async def stop_typing(self, target_id: str) -> None:
        if target_id in self._typing_tasks:
            task = self._typing_tasks.pop(target_id)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def get_tools_definition(self) -> str:
        tools_path = Path(__file__).parent / "discord_tools.md"
        return tools_path.read_text(encoding="utf-8") if tools_path.exists() else ""

    def get_tool_names(self) -> List[str]:
        return ["discord_send_dm", "discord_send_channel_message", "discord_add_reaction", "discord_lookup_user"]

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "discord_send_channel_message",
                    "description": "Send a message to a specific Discord channel.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "channel_id": {"type": "string", "description": "The ID of the Discord channel."},
                            "content": {"type": "string", "description": "The content of the message."}
                        },
                        "required": ["channel_id", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "discord_send_dm",
                    "description": "Send a Direct Message (DM) to a specific Discord user.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "string", "description": "The ID of the Discord user."},
                            "content": {"type": "string", "description": "The content of the DM."}
                        },
                        "required": ["user_id", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "discord_add_reaction",
                    "description": "Add an emoji reaction to a specific message.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "channel_id": {"type": "string", "description": "The ID of the channel."},
                            "message_id": {"type": "string", "description": "The ID of the message."},
                            "emoji": {"type": "string", "description": "The emoji to add."}
                        },
                        "required": ["channel_id", "message_id", "emoji"]
                    }
                }
            },
             {
                "type": "function",
                "function": {
                    "name": "discord_lookup_user",
                    "description": "Search for a Discord user by name or ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The username, display name, or ID."}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if tool_name == "discord_send_dm":
            return await self.send_dm(args.get("user_id"), args.get("content"))
        elif tool_name == "discord_lookup_user":
            return await self.lookup_user(args.get("query"))
        elif tool_name == "discord_send_channel_message":
            await self.send_channel_message(args.get("channel_id"), args.get("content"))
            return "Message sent."
        elif tool_name == "discord_add_reaction":
            await self.add_reaction(args.get("channel_id"), args.get("message_id"), args.get("emoji"))
            return "Reaction added."
        raise NotImplementedError(f"Tool {tool_name} not found in DiscordPact")
