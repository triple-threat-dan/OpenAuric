import pytest
import re
from auric.interface.adapters.discord import DiscordPact

def test_emoji_fixing_logic():
    # Test that naked emoji IDs are wrapped in brackets
    # Animated
    text1 = "Check this out a:cathi:1478099851047862436"
    fixed1 = DiscordPact._chunk_message(text1)[0]
    assert fixed1 == "Check this out <a:cathi:1478099851047862436>"

    # Static
    text2 = "Hello :smile:123456789012345678"
    fixed2 = DiscordPact._chunk_message(text2)[0]
    assert fixed2 == "Hello <:smile:123456789012345678>"

    # Already bracketed (should NOT change)
    text3 = "Already fine <a:cathi:1478099851047862436>"
    fixed3 = DiscordPact._chunk_message(text3)[0]
    assert fixed3 == "Already fine <a:cathi:1478099851047862436>"

    # Multiple emojis
    text4 = "a:emoji1:123 and :emoji2:456"
    fixed4 = DiscordPact._chunk_message(text4)[0]
    assert fixed4 == "<a:emoji1:123> and <:emoji2:456>"

    # Mixed with normal text and existing brackets
    text5 = "Look at a:cathi:123 and <a:fixed:456>"
    fixed5 = DiscordPact._chunk_message(text5)[0]
    assert fixed5 == "Look at <a:cathi:123> and <a:fixed:456>"
