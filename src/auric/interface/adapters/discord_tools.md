## Discord Tools
You have access to the following tools to interact with Discord.

- `discord_send_dm(user_id: str, content: str)`
  Sends a Direct Message (DM) to a specific user.
  - `user_id`: The ID of the user to message.
  - `content`: The text content of the message.

- `discord_send_channel_message(channel_id: str, content: str)`
  Sends a message to a specific Discord channel.
  - `channel_id`: The ID of the channel to message.
  - `content`: The text content of the message.

- `discord_add_reaction(channel_id: str, message_id: str, emoji: str)`
  Adds an emoji reaction to a specific message.
  - `channel_id`: The ID of the channel containing the message (use DM channel ID for DMs).
  - `message_id`: The ID of the message to react to.
  - `emoji`: The emoji character (e.g., "👍", "✅") or custom emoji code.
