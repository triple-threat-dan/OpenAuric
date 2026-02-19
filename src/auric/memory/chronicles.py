"""
The Chronicles & Dream Cycle.

This module contains the logic for the "Dream Cycle" — the agent's sleep phase
where it summarizes daily logs into long-term memory (Chronicles).
"""

import html
import json
import logging
from datetime import datetime, timedelta

import aiofiles
from auric.core.config import AURIC_ROOT

logger = logging.getLogger("auric.memory.chronicles")


async def _append_to_file(path, content: str) -> bool:
    """Appends content to a file if it exists. Returns True on success."""
    if not path.exists():
        logger.warning(f"{path.name} not found, skipping updates.")
        return False
    async with aiofiles.open(path, mode='a', encoding='utf-8') as f:
        await f.write(content)
    return True


def _unescape_list(items: list[str]) -> list[str]:
    """Unescape HTML entities from a list of strings."""
    return [html.unescape(s) for s in items]


async def perform_dream_cycle(audit_logger, gateway, config) -> None:
    """
    Executes the Dream Cycle — the agent's sleep phase.

    1. Summarizes the last active session if idle > 5 min.
    2. Reads and processes the daily log (YYYY-MM-DD.md).
    3. Extracts updates for MEMORY.md, USER.md, and HEARTBEAT.md.
    4. Optionally generates a creative dream story (DREAMS.md).
    """
    logger.info("Dream Cycle: Initiating...")

    # --- Step 1: Summarize idle session ---
    last_active_sid = await audit_logger.get_last_active_session_id()
    if last_active_sid:
        history = await audit_logger.get_chat_history(limit=1, session_id=last_active_sid)
        if history and (datetime.now() - history[0].timestamp) > timedelta(minutes=5):
            logger.info(f"Dream Cycle: Session {last_active_sid} inactive >5m. Summarizing.")
            try:
                hb_model = config.agents.models.get("heartbeat_model")
                model = hb_model.model if hb_model else config.agents.models["fast_model"].model
                await audit_logger.summarize_session(last_active_sid, gateway, model=model)
            except Exception as e:
                logger.error(f"Dream Cycle: Failed to summarize session: {e}")

    # --- Step 2: Process daily log ---
    today = datetime.now().strftime("%Y-%m-%d")
    daily_log_path = AURIC_ROOT / "memories" / f"{today}.md"

    if not daily_log_path.exists():
        logger.info("Dream Cycle: No daily log found. Skipping.")
        return

    async with aiofiles.open(daily_log_path, mode='r', encoding='utf-8') as f:
        content = await f.read()

    if not content.strip():
        logger.info("Dream Cycle: Daily log empty. Skipping.")
        return

    logger.info("Dream Cycle: Processing daily log for insights...")

    prompt = f"""
You are performing the "Dream Cycle" for the AI Agent.
Your goal is to process the daily memory log, clean it up, and extract long-term lessons.

CONTEXT:
 The daily log contains raw notes, session summaries, and task logs.
 Some info might be duplicated or irrelevant.

TASK:
1. **Clean Log**: Create a consolidated, clean version of the daily log. Remove duplicates, fix formatting. Remove any reminders/tasks/alarms from the log (they will be extracted separately).
2. **Extract Lessons**: Identify any critical information that should be stored in long-term memory (MEMORY.md) or user profiles (USER.md).
   - Lessons: general knowledge, world state, important facts.
   - User Info: preferences, names, specific user details.
3. **Extract Reminders/Tasks**: Identify any reminders, alarms, scheduled tasks, or to-do items that should be in HEARTBEAT.md.
   - These are things like "remind user to do X at Y time" or "check on Z tomorrow".
   - Do NOT include completed tasks — only pending/future ones.

INPUT LOG:
---
{content}
---

OUTPUT FORMAT (JSON):
{{
    "cleaned_daily_log": "...",
    "memory_updates": ["lesson 1", "lesson 2"],
    "user_updates": ["preference 1", "fact 2"],
    "heartbeat_updates": ["reminder 1", "scheduled task 2"]
}}

If no updates are needed for a category, return an empty list.
"""
    try:
        response = await gateway.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            tier="smart_model",
            response_format={"type": "json_object"}
        )

        data = json.loads(response.choices[0].message.content)

        cleaned_log = html.unescape(data.get("cleaned_daily_log", ""))
        memory_updates = _unescape_list(data.get("memory_updates", []))
        user_updates = _unescape_list(data.get("user_updates", []))
        heartbeat_updates = _unescape_list(data.get("heartbeat_updates", []))

        # --- Step 3: Apply updates ---

        # 3a. Overwrite daily log with cleaned version
        if cleaned_log:
            async with aiofiles.open(daily_log_path, mode='w', encoding='utf-8') as f:
                await f.write(f"{cleaned_log}\n\n**Dream Cycle Complete.**")

        # 3b. Append to MEMORY.md (staging section for agent to review & merge)
        if memory_updates:
            lines = [f"\n\n## Dream Cycle Notes ({today})\n"]
            lines.append("_Review and merge these into the sections above (Facts, Lessons Learned, People, etc.), then delete this section._\n")
            lines.extend(f"- {u}\n" for u in memory_updates)
            await _append_to_file(AURIC_ROOT / "memories" / "MEMORY.md", "".join(lines))

        # 3c. Append to USER.md (staging section for agent to review & merge)
        if user_updates:
            lines = [f"\n\n## Dream Cycle Notes ({today})\n"]
            lines.append("_Review and merge these into the profile above, then delete this section._\n")
            lines.extend(f"- {u}\n" for u in user_updates)
            await _append_to_file(AURIC_ROOT / "USER.md", "".join(lines))

        # 3d. Append to HEARTBEAT.md (reminders/tasks extracted from daily log)
        if heartbeat_updates:
            lines = [f"\n### Extracted from daily log ({today})\n"]
            lines.extend(f"  - {u}\n" for u in heartbeat_updates)
            if await _append_to_file(AURIC_ROOT / "HEARTBEAT.md", "".join(lines)):
                logger.info(f"Dream Cycle: Wrote {len(heartbeat_updates)} reminders/tasks to HEARTBEAT.md")

        logger.info(
            f"Dream Cycle: Completed. "
            f"{len(memory_updates)} memory, {len(user_updates)} user, {len(heartbeat_updates)} heartbeat updates."
        )

        # --- Step 4: Generate dream story (optional) ---
        if not config.agents.enable_dream_stories:
            logger.info("Dream Cycle: Dream stories disabled. Skipping.")
        else:
            try:
                dream_prompt = f"""
You are an AI agent who just fell asleep after a long day. Based on the following daily log, write a SHORT (3-6 sentences) but vivid, surreal, and wildly exaggerated dream story.

The dream should loosely reference real events from the day but twist them into absurd, fantastical scenarios. Be creative, funny, and weird. Write in first person as the agent dreaming.

Today's log:
---
{content}
---

Write ONLY the dream story, nothing else. No headers, no metadata.
"""
                dream_response = await gateway.chat_completion(
                    messages=[{"role": "user", "content": dream_prompt}],
                    tier="fast_model",
                    temperature=0.9
                )
                dream_story = dream_response.choices[0].message.content.strip()

                if dream_story:
                    dreams_path = AURIC_ROOT / "memories" / "DREAMS.md"
                    if not dreams_path.exists():
                        async with aiofiles.open(dreams_path, mode='w', encoding='utf-8') as f:
                            await f.write("# 💤 Dream Journal\n\n")

                    await _append_to_file(dreams_path, f"## {today}\n{dream_story}\n\n---\n\n")
                    logger.info("Dream Cycle: Dream story recorded to DREAMS.md 💤")

            except Exception as dream_err:
                logger.warning(f"Dream Cycle: Dream story failed (non-critical): {dream_err}")

    except Exception as e:
        logger.error(f"Dream Cycle: Error during processing: {e}")
