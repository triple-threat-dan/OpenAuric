
import asyncio
import logging
import hashlib
import json
import json
import re
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

from auric.core.config import AuricConfig, AURIC_WORKSPACE_DIR
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
        focus_manager: FocusManager,
        pact_manager: Any = None, # Typed as Any to avoid circular import for now
        tool_registry: Any = None
    ):
        self.config = config
        self.gateway = gateway
        self.librarian = librarian
        self.focus_manager = focus_manager
        self.pact_manager = pact_manager
        self.tool_registry = tool_registry
        
        self.session_cost = 0.0
        self.recursion_guard = RecursionGuard(config.agents.max_recursion)
        
        # Loop detection: Store hashes of (tool_name, arguments)
        self._action_history: List[str] = []
        self._max_history = 10 

    async def think(self, user_query: str, depth: int = 0, session_id: Optional[str] = None) -> str:
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
        messages = [{"role": "system", "content": system_prompt}]

        # Inject Chat History (only at depth 0)
        if depth == 0 and session_id and self.gateway.audit_logger:
            # Get recent 10 messages for context
            history = await self.gateway.audit_logger.get_chat_history(limit=10, session_id=session_id)
            for msg in history:
                if msg.role in ["USER", "AGENT"]:
                    role = "user" if msg.role == "USER" else "assistant"
                    messages.append({"role": role, "content": msg.content})

        messages.append({"role": "user", "content": user_query})

        # Per requirements, we need to handle `spawn_sub_agent`. 
        # Since we don't have a rigid Tool definition system in this file yet (it's injected via prompt or gateway),
        # we will assume the LLM might return a tool call in the text or structured output.
        # For simplicity and sticking to the prompt's "Sub-agents receive a filtered snapshot",
        # We'll use the smart model.

        try:
            response = await self.gateway.chat_completion(
                messages=messages,
                tier="smart",
                session_id=session_id,
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

        if not tool_calls and content:
            # Fallback: Parse Markdown JSON code blocks
            tool_calls = self._parse_json_tool_calls(content)

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
                    
                    # Inject result back
                    return f"Sub-Agent Result: {sub_result}\n\nParent Analysis: {content}"
                
                # Dynamic Tool Execution for Pacts
                elif self.pact_manager:
                    # Generic Pact Tool Execution
                    try:
                        # Attempt to execute via PactManager
                        # We don't filter by name here, we let PactManager decide if it owns the tool
                        # But we should probably check if it looks like a tool call we know?
                        # RLM doesn't know the tool names unless we cache them.
                        # However, based on the prompt, we just want to delegate.
                        try:
                            # Try Registry First (Internal Tools)
                            if self.tool_registry:
                                registry_result = await self.tool_registry.execute_tool(fn_name, args)
                                if "Tool" not in registry_result and "Error: Tool" not in registry_result: 
                                    # Very loose check, but execute_tool returns string. 
                                    # If it says "Error: Tool ... not found", we continue to Pact.
                                    # Actually, let's just try running it.
                                    pass

                            # Better logic: Check if it's an internal tool
                            if self.tool_registry and fn_name in self.tool_registry._internal_tools: 
                                result = await self.tool_registry.execute_tool(fn_name, args)
                                return f"Tool {fn_name} executed: {result}"
                            
                            # Fallback to Pact
                            result = await self.pact_manager.execute_tool(fn_name, args)
                            return f"Tool {fn_name} executed successfully: {result}"
                        except ValueError:
                             # Tool not found in pacts, maybe it's something else?
                             pass
                    except Exception as e:
                        logger.error(f"Failed to execute {fn_name}: {e}")
                        return f"Error executing {fn_name}: {e}"
        
        return content

    def _parse_json_tool_calls(self, content: str) -> List[Any]:
        """
        Fallback parser for markdown-wrapped JSON tool calls.
        """
        # Look for ```json { ... } ``` blocks
        pattern = r"```json\s*(\{.*?\})\s*```"
        matches = re.findall(pattern, content, re.DOTALL)
        
        parsed_tools = []
        for json_str in matches:
            try:
                # Clean up newlines or comments if necessary, usually json.loads is strict
                # Some models might add comments inside JSON, but specific regex for strict JSON is hard.
                # We assume valid JSON block.
                data = json.loads(json_str)
                
                if "name" in data and "arguments" in data:
                    # RLM expects tool_call.function.name and .arguments (as string)
                    args_str = json.dumps(data["arguments"]) if isinstance(data["arguments"], dict) else str(data["arguments"])
                    
                    tool_call = SimpleNamespace(
                        function=SimpleNamespace(
                            name=data["name"],
                            arguments=args_str
                        )
                    )
                    parsed_tools.append(tool_call)
            except Exception as e:
                logger.warning(f"Failed to parse fallback tool JSON: {e}")
                
        return parsed_tools

    def _assemble_system_prompt(self, task_context: TaskContext) -> str:
        """
        Builds the dynamic system prompt.
        Order: AGENT -> SOUL -> USER (Depth 0) -> TIME -> TOOLS -> MEMORY -> FOCUS
        """
        parts = []

        # 0. The Agent (Core Requirements)
        agent_path = AURIC_WORKSPACE_DIR / "AGENT.md"
        if agent_path.exists():
             parts.append(agent_path.read_text(encoding="utf-8"))
        else:
             parts.append("You are OpenAuric, a recursive AI agent.")

        # 1. The Soul (Personality)
        soul_path = AURIC_WORKSPACE_DIR / "SOUL.md"
        if soul_path.exists():
            parts.append(soul_path.read_text(encoding="utf-8"))

        # 2. The User (Only at Depth 0)
        if task_context.depth == 0:
            user_path = AURIC_WORKSPACE_DIR / "USER.md"
            if user_path.exists():
                parts.append(f"## User Context\n{user_path.read_text(encoding='utf-8')}")

        # 3. The Time
        parts.append(f"## Current Time\n{datetime.now().isoformat()}")

        # 4 Memory & Abilities
        memory_path = AURIC_WORKSPACE_DIR / "grimoire" / "MEMORY.md"
        if memory_path.exists():
            parts.append(memory_path.read_text(encoding="utf-8"))

        abilities_path = AURIC_WORKSPACE_DIR / "grimoire" / "ABILITIES.md"
        if abilities_path.exists():
            parts.append(abilities_path.read_text(encoding="utf-8"))

        # 5. The Tools
        # In a real system, we'd iterate over available tools and dump their schemas.
        # For now, we inject a generic placeholder or specific instructions.
        parts.append("""
## Available Tools
You have access to the following tools. Use them to solve the user's request.

### Tool Usage Instructions
1. To call a tool, output ONLY the JSON code block representing the tool call.
2. Do not provide commentary before or after the JSON.
3. If you output a tool call, your turn ends immediately. Do not generate further text.
4. Format:
```json
{
  "name": "tool_name",
  "arguments": {
    "arg_name": "value"
  }
}
```

### Native Tools
- spawn_sub_agent(instruction: str): clear_instruction -> str
  Use this to delegate complex sub-tasks to a recursive instance of yourself. 
  Do not use if the task is simple.
""")
        # Inject Pact Tools
        if self.pact_manager:
            pact_tools = self.pact_manager.get_all_tools_definitions()
            if pact_tools:
                parts.append(pact_tools)

        # Inject Registry Tools
        if self.tool_registry:
            registry_schemas = self.tool_registry.get_tools_schema()
            if registry_schemas:
                parts.append(f"## Registry Tools:\n{json.dumps(registry_schemas, indent=2)}")
        

        # 6. The Memory (Snapshot)
        if task_context.relevant_snippets:
            parts.append("## Relevant Context (Grimoire)")
            for snippet in task_context.relevant_snippets:
                parts.append(snippet)

        # 7. The Focus (Working Memory)
        focus_path = AURIC_WORKSPACE_DIR / "grimoire" / "FOCUS.md"
        if focus_path.exists():
            parts.append(focus_path.read_text(encoding="utf-8"))
        
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
