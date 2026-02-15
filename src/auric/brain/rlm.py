import logging
import hashlib
import json
import json
import re
import inspect
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from pydantic import BaseModel, Field

from auric.core.config import AuricConfig, AURIC_WORKSPACE_DIR, AURIC_ROOT, AURIC_ROOT
from auric.brain.llm_gateway import LLMGateway
from auric.memory.librarian import GrimoireLibrarian
from auric.memory.focus_manager import FocusManager
from auric.spells.tool_registry import ToolRegistry

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
        pact_manager: Optional[Any] = None, # Avoid circular import if needed
        tool_registry: Optional[ToolRegistry] = None,
        log_callback: Optional[Callable[[str, str], None]] = None
    ):
        self.config = config
        self.gateway = gateway
        self.librarian = librarian
        self.focus_manager = focus_manager
        self.pact_manager = pact_manager
        self.tool_registry = tool_registry
        self.log_callback = log_callback
        
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

        # 4. ReAct Loop
        # We loop up to max_turns to allow for multi-step reasoning.
        max_turns = 10
        current_turn = 0
        
        # We start with the base messages
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

        final_response = ""

        # Collect Tool Schemas
        tools_schemas = []
        if self.tool_registry:
            tools_schemas.extend(self.tool_registry.get_tools_schema())
        
        # Inject Pact Tools
        if self.pact_manager:
            tools_schemas.extend(self.pact_manager.get_tools_schema())
        
        # Add spawn_sub_agent manually
        tools_schemas.append({
            "type": "function",
            "function": {
                "name": "spawn_sub_agent",
                "description": "Delegates a complex sub-task to a recursive sub-agent. The sub-agent has its own context and tools. Use this to break down large tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instruction": {
                            "type": "string",
                            "description": "The specific instruction for the sub-agent to execute."
                        }
                    },
                    "required": ["instruction"]
                }
            }
        })
        
        # If no tools, pass None
        tools_arg = tools_schemas if tools_schemas else None

        while current_turn < max_turns:
            current_turn += 1
            
            # Dynamic Context Refresh: Re-assemble system prompt to capture state changes (e.g. FOCUS.md edits)
            # This allows the agent to "see" its own writes without needing to read the file back.
            system_prompt = self._assemble_system_prompt(task_context)
            messages[0]["content"] = system_prompt
            
            try:
                response = await self.gateway.chat_completion(
                    messages=messages,
                    tier="smart",
                    session_id=session_id,
                    tools=tools_arg
                )
            except Exception as e:   
                logger.error(f"LLM Call Failed: {e}")
                raise

            # Track Cost
            self._track_cost(response)

            content = response.choices[0].message.content or ""
            tool_calls = getattr(response.choices[0].message, "tool_calls", None)
            is_fallback = False

            # Fallback for structured content in text
            if not tool_calls and content:
                tool_calls = self._parse_json_tool_calls(content)
                if tool_calls:
                    is_fallback = True

            # If no tool calls, we are done
            if not tool_calls:
                final_response = content
                return final_response

            # Append Assistant's thought/tool-call to history
            messages.append(response.choices[0].message)

            # Process Tool Calls
            for tool_call in tool_calls:
                fn_name = tool_call.function.name
                # Fallback parser might give arguments as dict already or string
                # _parse_json_tool_calls converts to string to match native object, 
                # but let's be safe.
                args_val = tool_call.function.arguments
                if isinstance(args_val, str):
                    try:
                        args = json.loads(args_val)
                    except json.JSONDecodeError:
                         # Try cleaning? Or just fail.
                         logger.error(f"Failed to parse arguments for {fn_name}")
                         args = {} 
                else:
                    args = args_val

                
                # Check for loops
                self._check_loop(fn_name, args)
                
                # Log tool execution to CLI/Bus via callback
                if hasattr(self, "log_callback") and self.log_callback:
                    log_msg = f"Executing {fn_name}"
                    # Add args if concise, or maybe just simple name
                    # Let's show args for clarity
                    log_msg += f" with args: {json.dumps(args)}"
                    
                    if inspect.iscoroutinefunction(self.log_callback):
                        await self.log_callback("TOOL", log_msg)
                    else:
                        self.log_callback("TOOL", log_msg)

                result_content = ""

                if fn_name == "spawn_sub_agent":
                    instruction = args.get("instruction")
                    logger.info(f"Spawning Sub-Agent: {instruction[:50]}...")
                    # RECURSION HAPPENS HERE
                    sub_result = await self.think(instruction, depth=depth + 1)
                    result_content = f"Sub-Agent Result: {sub_result}"
                
                else:
                    # Dynamic Tool Execution
                    try:
                        # Try Registry First (Internal Tools)
                        if self.tool_registry and fn_name in self.tool_registry._internal_tools: 
                            result = await self.tool_registry.execute_tool(fn_name, args)
                            result_content = f"Tool {fn_name} executed: {result}"
                        elif self.tool_registry and fn_name in self.tool_registry._spells:
                             # Check if it's a spell
                             result = await self.tool_registry.execute_tool(fn_name, args)
                             result_content = f"Spell {fn_name} executed: {result}"
                        elif self.pact_manager:
                            # Fallback to Pact
                            result = await self.pact_manager.execute_tool(fn_name, args)
                            result_content = f"Tool {fn_name} executed successfully: {result}"
                        else:
                             result_content = f"Error: Tool {fn_name} not found."
                    except Exception as e:
                        logger.error(f"Failed to execute {fn_name}: {e}")
                        result_content = f"Error executing {fn_name}: {e}"

                # Log Tool Result to CLI/Bus
                if hasattr(self, "log_callback") and self.log_callback:
                    # Truncate long results for display
                    display_result = result_content[:500] + "..." if len(result_content) > 500 else result_content
                    log_msg = f"Result from {fn_name}: {display_result}"
                    
                    if inspect.iscoroutinefunction(self.log_callback):
                        await self.log_callback("TOOL", log_msg) # Reuse TOOL level for now
                    else:
                        self.log_callback("TOOL", log_msg)

                # Append Tool Result to messages
                if is_fallback:
                    # Fallback calls are not registered in the assistant message as authentic tool calls.
                    # We must reply as User to continue the conversation flow.
                    messages.append({
                        "role": "user",
                        "content": f"Tool '{fn_name}' Result: {result_content}"
                    })
                else:
                    # Native tool call requires matching ID
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": fn_name,
                        "content": result_content
                    })
        
        return final_response if final_response else "Task completed (max turns reached)."

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
                    
                    import uuid
                    tool_call = SimpleNamespace(
                        id=f"call_{uuid.uuid4().hex[:8]}", # Dummy ID
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
        agent_path = AURIC_ROOT / "AGENT.md"
        if agent_path.exists():
             parts.append(agent_path.read_text(encoding="utf-8"))
        else:
             parts.append("You are OpenAuric, a recursive AI agent.")

        # 1. The Soul (Personality)
        soul_path = AURIC_ROOT / "SOUL.md"
        if soul_path.exists():
            parts.append(soul_path.read_text(encoding="utf-8"))

        # 2. The User (Only at Depth 0)
        if task_context.depth == 0:
            user_path = AURIC_ROOT / "USER.md"
            if user_path.exists():
                parts.append(f"## User Context\n{user_path.read_text(encoding='utf-8')}")

        # 3. The Time
        parts.append(f"## Current Time\n{datetime.now().isoformat()}")

        # 4 Memory & Abilities
        memory_path = AURIC_ROOT / "memories" / "MEMORY.md"
        if memory_path.exists():
            parts.append(memory_path.read_text(encoding="utf-8"))

        spells_path = AURIC_ROOT / "grimoire" / "SPELLS.md"
        if spells_path.exists():
            parts.append(spells_path.read_text(encoding="utf-8"))

        # 5. The Tools
        # In a real system, we'd iterate over available tools and dump their schemas.
        # For now, we inject a generic placeholder or specific instructions.
        # 5. The Tools
        # We use Native Function Calling. 
        # However, we can list high-level capabilities here if needed.
        parts.append("## Available Tools\nYou have access to native tools for file operations, shell execution, and python coding. Use them when needed.")

        # Inject Pact Tools
        if self.pact_manager:
            pact_tools = self.pact_manager.get_all_tools_definitions()
            if pact_tools:
                parts.append(pact_tools)

        # Registry Tools are now passed natively to the LLM context.
        # We do not dump their schemas into the text prompt anymore.
        

        # 6. The Memory (Snapshot)
        if task_context.relevant_snippets:
            parts.append("## Relevant Context (Grimoire)")
            for snippet in task_context.relevant_snippets:
                parts.append(snippet)

        # 7. The Focus (Working Memory)
        focus_path = AURIC_ROOT / "memories" / "FOCUS.md"
        if focus_path.exists():
            parts.append(focus_path.read_text(encoding="utf-8"))
        
        return "\n\n".join(parts)

    def _track_cost(self, response: Any):
        """
        Accumulate token costs.
        Uses litellm's calculated cost if available, otherwise estimates.
        """
        cost = 0.0
        
        # 1. Try to get exact cost from litellm hidden params
        if hasattr(response, "_hidden_params"):
            cost = response._hidden_params.get("response_cost", 0.0)
        
        # 2. Fallback to usage calculation if cost is 0 (local models or unsupported provider)
        if cost == 0.0:
            usage = getattr(response, "usage", None)
            if usage:
                 # Check if it's likely a local model or just missing cost data
                 # We'll use a very low cost for fallback to avoid blocking users
                 # $2.00 per 1M tokens (Approximate average of cheap models)
                 total_tokens = getattr(usage, "total_tokens", 0)
                 cost = (total_tokens / 1_000_000) * 2.00

        self.session_cost += cost
        logger.debug(f"Turn Cost: ${cost:.6f} | Total Session Cost: ${self.session_cost:.4f}")

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
