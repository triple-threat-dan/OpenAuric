"""
The Chronicles & Dream Cycle.

This module will contain the logic for the "Dream Cycle" - the agent's sleep phase
where it summarizes daily logs into long-term memory (Chonicles).
"""


import logging
from datetime import datetime, timedelta
from pathlib import Path
import asyncio

import aiofiles
from auric.core.config import AURIC_ROOT

logger = logging.getLogger("auric.memory.chronicles")

async def perform_dream_cycle(audit_logger, gateway, config) -> None:
    """
    Executes the "Dream Cycle" - the agent's sleep phase.
    
    1. Checks if current session is inactive (>5m) and summarizes it if needed.
    2. Reads the daily memory log (YYYY-MM-DD.md).
    3. Uses LLM to clean up, format, and extract long-term lessons.
    4. Updates MEMORY.md or USER.md with extracted lessons.
    """
    logger.info("Dream Cycle: Initiating...")
    
    # --- Step 1: Session Check & Summarization ---
    # We want to summarize the *current* session if it's been inactive.
    # The new_session endpoint handles summarization when *switching*, 
    # but if the agent is just left running, we want to capture that state.

    current_sid = None
    # We need to access the current session ID. 
    # In daemon.py, it's stored in api_app.state.current_session_id.
    # But here we only have audit_logger, gateway, config.
    # We can ask audit_logger for the last active session ID.
    
    last_active_sid = await audit_logger.get_last_active_session_id()
    if last_active_sid:
        # Check last message time
        history = await audit_logger.get_chat_history(limit=1, session_id=last_active_sid)
        if history:
            last_msg_time = history[0].timestamp
            now = datetime.now()
            if (now - last_msg_time) > timedelta(minutes=5):
                logger.info(f"Dream Cycle: Session {last_active_sid} inactive for >5m. Triggering summary.")
                # Summarize session
                # Note: summarize_session appends to daily log.
                # We should potentially check if it was already summarized to avoid dupes?
                # For now, relying on the fact that summarize_session is idempotent-ish 
                # (it just appends notes).
                
                # Check if we recently summarized this session in the daily log?
                # That's hard to parsing text.
                # Let's just do it. The cleanup step (Step 2) will handle dupes.
                try:
                    # Use heartbeat_model if available, otherwise fallback to fast_model
                    hb_model = config.agents.models.get("heartbeat_model")
                    model_to_use = hb_model.model if hb_model else config.agents.models["fast_model"].model
                    
                    await audit_logger.summarize_session(last_active_sid, gateway, model=model_to_use)
                except Exception as e:
                    logger.error(f"Dream Cycle: Failed to summarize session: {e}")

    # --- Step 2: Daily Log Processing ---
    today = datetime.now().strftime("%Y-%m-%d")
    daily_log_path = AURIC_ROOT / "memories" / f"{today}.md"
    
    if not daily_log_path.exists():
        logger.info("Dream Cycle: No daily log found. Skipping cleanup.")
        return

    async with aiofiles.open(daily_log_path, mode='r', encoding='utf-8') as f:
        content = await f.read()

    if not content.strip():
        logger.info("Dream Cycle: Daily log empty. Skipping.")
        return

    logger.info("Dream Cycle: Processing daily log for insights...")
    
    # Prompt LLM to clean and extract
    prompt = f"""
You are performing the "Dream Cycle" for the AI Agent.
Your goal is to process the daily memory log, clean it up, and extract long-term lessons.

CONTEXT:
 The daily log contains raw notes, session summaries, and task logs.
 Some info might be duplicated or irrelevant.

TASK:
1. **Clean Log**: Create a consolidated, clean version of the daily log. Remove duplicates, fix formatting.
2. **Extract Lessons**: Identify any critical information that should be stored in long-term memory (MEMORY.md) or user profiles (USER.md).
   - Lessons: general knowledge, world state, important facts.
   - User Info: preferences, names, specific user details.

INPUT LOG:
---
{content}
---

OUTPUT FORMAT (JSON):
{{
    "cleaned_daily_log": "...",
    "memory_updates": ["lesson 1", "lesson 2"],
    "user_updates": ["preference 1", "fact 2"]
}}

If no updates are needed for a category, return an empty list.
"""
    try:
        messages = [{"role": "user", "content": prompt}]
        
        smart_model_id = config.agents.models["smart_model"].model
        
        response = await gateway.chat_completion(
            messages=messages,
            tier="smart_model", # Gateway expects the key in models dict, which is "smart_model"
            response_format={"type": "json_object"}
        )
        
        result_json = response.choices[0].message.content
        import json
        import html
        data = json.loads(result_json)
        
        cleaned_log = html.unescape(data.get("cleaned_daily_log", ""))
        memory_updates = [html.unescape(u) for u in data.get("memory_updates", [])]
        user_updates = [html.unescape(u) for u in data.get("user_updates", [])]
        
        # --- Step 3: Apply Updates ---
        
        # 1. Overwrite Daily Log with Cleaned Version
        if cleaned_log:
            async with aiofiles.open(daily_log_path, mode='w', encoding='utf-8') as f:
                await f.write(cleaned_log)
                await f.write("\n\n**Dream Cycle Complete.**")
        
        # 2. Append to MEMORY.md
        if memory_updates:
            memory_path = AURIC_ROOT / "memories" / "MEMORY.md"
            if memory_path.exists():
                async with aiofiles.open(memory_path, mode='a', encoding='utf-8') as f:
                    await f.write(f"\n\n### Learned on {today}\n")
                    for update in memory_updates:
                        await f.write(f"- {update}\n")
            else:
                 logger.warning("MEMORY.md not found, skipping updates.")

        # 3. Append to USER.md
        if user_updates:
            user_path = AURIC_ROOT / "USER.md"
            if user_path.exists():
                async with aiofiles.open(user_path, mode='a', encoding='utf-8') as f:
                    await f.write(f"\n\n### Updated on {today}\n")
                    for update in user_updates:
                        await f.write(f"- {update}\n")
            else:
                 logger.warning("USER.md not found, skipping updates.")

        logger.info(f"Dream Cycle: Completed. {len(memory_updates)} memory updates, {len(user_updates)} user updates.")

    except Exception as e:
        logger.error(f"Dream Cycle: Error during processing: {e}")

