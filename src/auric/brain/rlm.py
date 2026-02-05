
import asyncio
import logging
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

from auric.core.config import AuricConfig
from auric.brain.llm_gateway import LLMGateway
from auric.memory.librarian import GrimoireLibrarian
from auric.memory.focus_manager import FocusManager

logger = logging.getLogger("auric.brain.rlm")

# ==============================================================================
# Exceptions
# ==============================================================================

class RecursionLimitExceeded(Exception):
    """Raised when the agent attempts to go deeper than allowed."""
    pass

class CostLimitExceeded(Exception):
    """Raised when the session cost exceeds the limit."""
    pass

class RepetitiveStressError(Exception):
    """Raised when the agent enters a repetitive loop."""
    pass

# ==============================================================================
# Data Models
# ==============================================================================

class TaskContext(BaseModel):
    """Holds the dynamic context for a specific thought step."""
    query: str
    relevant_snippets: List[str] = Field(default_factory=list)
    depth: int = 0
    parent_instruction: Optional[str] = None

# ==============================================================================
# Recursion Guard
# ==============================================================================

class RecursionGuard:
    """
    Helper to enforce recursion depth limits.
    """
    def __init__(self, max_depth: int):
        self.max_depth = max_depth

    def check(self, current_depth: int):
        if current_depth > self.max_depth:
            raise RecursionLimitExceeded(
                f"Maximum recursion depth ({self.max_depth}) exceeded at depth {current_depth}."
            )

# ==============================================================================
# RLM Engine
# ==============================================================================

class RLMEngine:
    """
    The Recursive Language Model (RLM) Engine.
    Orchestrates the agent's thought process, context assembly, and recursive execution.
    """

    def __init__(
        self, 
        config: AuricConfig,
        gateway: LLMGateway,
        librarian: GrimoireLibrarian,
        focus_manager: FocusManager
    ):
        self.config = config
        self.gateway = gateway
        self.librarian = librarian
        self.focus_manager = focus_manager
        
        self.session_cost = 0.0
        self.recursion_guard = RecursionGuard(config.agents.max_recursion)
        
        # Loop detection: Store hashes of (tool_name, arguments)
        self._action_history: List[str] = []
        self._max_history = 10 

    async def think(self, user_query: str, depth: int = 0) -> str:
        """
        The main recursive loop.
        1. Checks safeguards (Depth, Cost).
        2. Gathers context (Focus-Shift).
        3. Assembles System Prompt.
        4. Calls LLM.
        5. Handles Recursive Tool Calls (spawn_sub_agent).
        """
        # 1. Safeguards
        self.recursion_guard.check(depth)
        if self.session_cost > self.config.agents.max_cost:
            raise CostLimitExceeded(f"Session cost ${self.session_cost:.2f} exceeds limit ${self.config.agents.max_cost}")

        logger.info(f"RLM Thinking (Depth {depth}): {user_query[:50]}...")

        # 2. Focus-Shift: Gather Context
        # We query the librarian for relevant knowledge based on the user query
        snippets = self.librarian.search(user_query)
        task_context = TaskContext(
            query=user_query,
            relevant_snippets=snippets,
            depth=depth
        )

        # 3. Assemble System Prompt
        system_prompt = self._assemble_system_prompt(task_context)

        # 4. Call LLM
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ]

        # In a real implementation, we would define tools here.
        # For this ticket, we mock the recursive capability logic if no tool definitions are present yet,
        # or we assume the LLMGateway handles tool definitions if passed.
        # But per requirements, we need to handle `spawn_sub_agent`. 
        # Since we don't have a rigid Tool definition system in this file yet (it's injected via prompt or gateway),
        # we will assume the LLM might return a tool call in the text or structured output.
        # For simplicity and sticking to the prompt's "Sub-agents receive a filtered snapshot",
        # We'll use the smart model.

        try:
            response = await self.gateway.chat_completion(
                messages=messages,
                tier="smart",
                # We would verify tools here, e.g. tools=[spawn_sub_agent_schema]
            )
        except Exception as e:
            logger.error(f"LLM Call Failed: {e}")
            raise

        # Track Cost (Mock calculation)
        self._track_cost(response)

        # 5. Process Output & Handle Recursion
        # Note: Litellm response format handling would go here.
        # We check if the model wants to call 'spawn_sub_agent'.
        
        # This is a simplified logic to demonstrate the recursion pattern
        # In a full production system, we'd parse tool_calls properly.
        content = response.choices[0].message.content or ""
        tool_calls = getattr(response.choices[0].message, "tool_calls", None)

        if tool_calls:
            for tool_call in tool_calls:
                fn_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                
                # Check for loops
                self._check_loop(fn_name, args)

                if fn_name == "spawn_sub_agent":
                    instruction = args.get("instruction")
                    logger.info(f"Spawning Sub-Agent: {instruction[:50]}...")
                    
                    # RECURSION HAPPENS HERE
                    sub_result = await self.think(instruction, depth=depth + 1)
                    
                    # Inject result back (in a real chat loop, we'd append to messages and call again)
                    # For this single-turn `think` method, we might just return the result or 
                    # create a new context. 
                    # Returning the sub-agent's result combined with current might be expected.
                    return f"Sub-Agent Result: {sub_result}\n\nParent Analysis: {content}"
        
        return content

    def _assemble_system_prompt(self, task_context: TaskContext) -> str:
        """
        Builds the dynamic system prompt.
        Order: SOUL -> USER (Depth 0) -> TIME -> FOCUS -> TOOLS -> MEMORY
        """
        parts = []

        # 1. The Soul
        soul_path = Path.home() / ".auric" / "grimoire" / "SOUL.md"
        if soul_path.exists():
            parts.append(soul_path.read_text(encoding="utf-8"))
        else:
            parts.append("You are OpenAuric, a recursive AI agent.")

        # 2. The User (Only at Depth 0)
        if task_context.depth == 0:
            user_path = Path.home() / ".auric" / "grimoire" / "USER.md"
            if user_path.exists():
                parts.append(f"## User Context\n{user_path.read_text(encoding='utf-8')}")

        # 3. The Time
        parts.append(f"## Current Time\n{datetime.now().isoformat()}")

        # 4. The Focus (Working Memory)
        # We assume FocusManager has the current state loaded or we load it.
        # Since FocusManager is stateful, we can ask it for the current text representation
        # but the class provided has `load` which reads from file.
        # Let's read the file directly or use the manager if it had a 'get_content' method.
        # The manager has `load()` which returns a model. simpler to read the file for the raw prompt injection
        # to ensure we get the exact markdown structure.
        focus_path = Path.home() / ".auric" / "FOCUS.md"
        if focus_path.exists():
            parts.append(focus_path.read_text(encoding="utf-8"))

        # 5. The Tools
        # In a real system, we'd iterate over available tools and dump their schemas.
        # For now, we inject a generic placeholder or specific instructions.
        parts.append("""
## Available Tools
You have access to the following tools. Use them to solve the user's request.
- spawn_sub_agent(instruction: str): clear_instruction -> str
  Use this to delegate complex sub-tasks to a recursive instance of yourself. 
  Do not use if the task is simple.
""")

        # 6. The Memory (Snapshot)
        if task_context.relevant_snippets:
            parts.append("## Relevant Context (Grimoire)")
            for snippet in task_context.relevant_snippets:
                parts.append(snippet)
        
        return "\n\n".join(parts)

    def _track_cost(self, response: Any):
        """
        Accumulate token costs.
        This is a rough estimation or extraction from the response usage fields.
        """
        usage = getattr(response, "usage", None)
        if usage:
            # Mock pricing: $10.00 per 1M tokens (blended)
            total_tokens = getattr(usage, "total_tokens", 0)
            cost = (total_tokens / 1_000_000) * 10.00
            self.session_cost += cost

    def _check_loop(self, tool_name: str, args: Dict[str, Any]):
        """
        Detects repetitive tool calls.
        """
        # Create a determinstic hash of the call
        call_str = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        call_hash = hashlib.md5(call_str.encode()).hexdigest()

        self._action_history.append(call_hash)
        if len(self._action_history) > self._max_history:
            self._action_history.pop(0)

        # Check for 3 repeats in a row
        if len(self._action_history) >= 3:
            if self._action_history[-1] == self._action_history[-2] == self._action_history[-3]:
                raise RepetitiveStressError(f"Detected infinite loop for tool {tool_name} with args {args}")
