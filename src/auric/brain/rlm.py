import logging
import hashlib
import json
import os
import re
import inspect
import platform
import uuid
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from pydantic import BaseModel, Field

from auric.core.config import AuricConfig, AURIC_WORKSPACE_DIR, AURIC_ROOT
from auric.brain.llm_gateway import LLMGateway
from auric.memory.librarian import GrimoireLibrarian
from auric.memory.focus_manager import FocusManager
from auric.spells.tool_registry import ToolRegistry
from auric.core.system_logger import SystemLogger

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
        
        # Cache logger instance
        self.system_logger = SystemLogger.get_instance()

        self._action_history: List[str] = []
        self._max_history = 10 

    async def check_heartbeat_necessity(self, user_query: str) -> bool:
        """
        Performs a 'Lean Check' to see if the full agent is needed.
        Returns True if there is actionable content in the heartbeat message.
        """
        # 1. Trivial check: If empty or just headers
        clean_query = user_query.strip()
        if not clean_query:
            return False
            
        # 2. Lean LLM Check
        # We use the configured 'heartbeat_model' (likely a cheaper/faster model)
        # to classify the text.
        
        # Helper to get time of day
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 12:
            period = "Morning"
        elif 12 <= hour < 17:
            period = "Afternoon"
        elif 17 <= hour < 21:
            period = "Evening"
        else:
            period = "Night"
            
        system_prompt = (
            "You are the Heartbeat Monitor for OpenAuric.\n"
            "**GOAL**: Determine if the provided `HEARTBEAT.md` content contains **any** *actionable* tasks *for Current Time*.\n\n"
            "**CONSTRAINTS**:\n"
            "1. **Ignore Structure**: Ignore HTML comments (`<!-- -->`), and headers (`#`).\n"
            "2. **Check for Actionable Tasks**: Look for instructions (e.g., '- Check database', '- Every morning...').\n"
            "3. **STRICT TIME CHECK**: Compare the task's time condition against Current Time.\n"
            "   - If task says '9am' and it is 00:30 -> NO.\n"
            "   - If task says 'Every evening' and it is Morning -> NO.\n"
            "   - If task says 'at 5pm' and it is 5:00pm -> YES.\n"
            "4. **Output Format**: \n"
            "   - Scan ALL tasks.\n"
            "   - **STOP IMMEDIATELY** if you find a task that is actionable NOW.\n"
            "   - Output: 'Task: [Brief Text] -> [Analysis] -> VERDICT: YES'\n"
            "   - If no tasks are actionable after scanning all, output 'VERDICT: NO'."
            "5. **Output Format**: ONLY output the VERDICT. No other text (e.g. 'VERDICT: YES' or 'VERDICT: NO').\n"
            f"Current Time: {now.astimezone().strftime('%Y-%m-%d %I:%M %p %Z')} ({period})\n\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Analyze this content:\n---\n{clean_query}\n---"}
        ]

        try:
            response = await self.gateway.chat_completion(
                messages=messages,
                tier="fast_model", 
                # Allow enough tokens for a single positive analysis or a few negatives
                max_tokens=1000 
            )
            
            # Track cost for this lean check too
            try:
                self._track_cost(response)
            except Exception as cost_err:
                logger.warning(f"Failed to track cost for heartbeat check: {cost_err}")
            
            content = response.choices[0].message.content.strip().upper()
            logger.info(f"Heartbeat Lean Check:\n{content}")
            
            return "VERDICT: YES" in content
            
        except Exception as e:
            logger.error(f"Heartbeat Lean Check Failed: {e}")
            # Fail safe: if check fails, assume we should wake up (better to be noisy than miss a task)
            return True 

    async def think(self, user_query: str, depth: int = 0, session_id: Optional[str] = None, model_tier: str = "smart_model") -> str:
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
        
        # LOGGING
        self.system_logger.log("THOUGHT_START", {"query": user_query, "depth": depth, "session_id": session_id}, session_id=session_id)


        # 2. Focus-Shift: Gather Context
        # We NO LONGER automatically query the librarian for relevant knowledge.
        # The agent must explicitly use the memory_search tool if it needs past context.
        snippets = []
        
        task_context = TaskContext(
            query=user_query,
            relevant_snippets=snippets,
            depth=depth
        )

        # 3. Assemble System Prompt
        system_prompt = self._assemble_system_prompt(task_context)

        # 4. ReAct Loop
        # We loop up to max_turns to allow for multi-step reasoning.
        max_turns = self.config.agents.max_turns
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
        
        # Add spawn_sub_agent only if there's room in the recursion budget.
        # Sub-agents near max depth should NOT see this tool, forcing them to
        # produce content directly instead of endlessly delegating.
        max_depth = self.config.agents.max_recursion
        if depth + 1 <= max_depth:
            tools_schemas.append(self._get_recursion_tool_schema())
        
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
                    tier=model_tier,
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
                self.system_logger.log("THOUGHT_END", {"response": final_response, "cost": self.session_cost}, session_id=session_id)
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
                    
                    self.system_logger.log("TOOL_CALL", {"name": fn_name, "args": args}, session_id=session_id)
                    
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
                        elif self.pact_manager and fn_name in self.pact_manager.get_tool_names():
                            result = await self.pact_manager.execute_tool(fn_name, args)
                            result_content = f"Tool {fn_name} executed successfully: {result}"
                        else:
                            # Unknown tool - give clear feedback with available tool names
                            available = self._get_available_tool_names()
                            result_content = (
                                f"ERROR: Tool '{fn_name}' does not exist. "
                                f"Do NOT invent tool names. "
                                f"Your available tools are: {', '.join(sorted(available))}"
                            )
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
        Fallback parser for markdown-wrapped JSON or XML-style tool calls.
        """
        parsed_tools = []
        import uuid
        
        # 1. Try JSON Blocks: ```json { ... } ```
        pattern_json = r"```json\s*(\{.*?\})\s*```"
        matches_json = re.findall(pattern_json, content, re.DOTALL)
        
        for json_str in matches_json:
            try:
                data = json.loads(json_str)
                if "name" in data and "arguments" in data:
                    args_str = json.dumps(data["arguments"]) if isinstance(data["arguments"], dict) else str(data["arguments"])
                    tool_call = SimpleNamespace(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        function=SimpleNamespace(name=data["name"], arguments=args_str)
                    )
                    parsed_tools.append(tool_call)
            except Exception as e:
                logger.warning(f"Failed to parse fallback tool JSON: {e}")

        # 2. Try XML-style: <functioninvoke> / <functioncall> / <|DSML|...>
        # This handles cases where the model hallucinates specialized XML formats
        # We look for either tag name, including the "DSML" variant with potential unicode pipes
        
        # Regex explanation:
        # < : start
        # (?: ... ) : non-capturing group for prefix options
        #   functioninvoke|functioncall : standard hallucinations
        #   | : OR
        #   [\uff5c|]DSML[\uff5c|](?:functioninvoke|functioncall|invoke) : DSML variant with normal or fullwidth pipe
        # \s+name=... : attribute matching
        pattern_xml = r"<(?:functioninvoke|functioncall|[\uff5c|]DSML[\uff5c|](?:functioninvoke|functioncall|invoke))\s+name=[\"'](?P<name>[\w_]+)[\"'][^>]*>(?P<args>.*?)</(?:functioninvoke|functioncall|[\uff5c|]DSML[\uff5c|](?:functioninvoke|functioncall|invoke))>"
        matches_xml = re.finditer(pattern_xml, content, re.DOTALL | re.IGNORECASE)
        
        for match in matches_xml:
            try:
                fn_name = match.group("name")
                inner_content = match.group("args")
                args = {}
                
                # Parse parameters: <parameter name="key">value</parameter>
                # OR <|DSML|parameter name="key"...>value</...>
                param_pattern = r"<(?:parameter|[\uff5c|]DSML[\uff5c|]parameter)\s+name=[\"'](?P<key>[\w_]+)[\"'][^>]*>(?P<value>.*?)</(?:parameter|[\uff5c|]DSML[\uff5c|]parameter)>"
                param_matches = re.finditer(param_pattern, inner_content, re.DOTALL | re.IGNORECASE)
                
                for pm in param_matches:
                    args[pm.group("key")] = pm.group("value").strip()
                
                tool_call = SimpleNamespace(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    function=SimpleNamespace(name=fn_name, arguments=json.dumps(args))
                )
                parsed_tools.append(tool_call)
                logger.info(f"Parsed XML tool call ({fn_name}): {args}")
            except Exception as e:
                 logger.warning(f"Failed to parse XML tool call: {e}")

        return parsed_tools

    def _assemble_system_prompt(self, task_context: TaskContext) -> str:
        """
        Builds the dynamic system prompt.
        Order: AGENT -> SOUL -> USER (Depth 0) -> TIME -> TOOLS -> MEMORY -> FOCUS
        """
        parts = []

        # 0. The Agent (Core Requirements)
        agent_text = self._read_section(AURIC_ROOT / "AGENT.md")
        parts.append(agent_text if agent_text else "You are OpenAuric, a recursive AI agent.")

        # 1. The Soul (Personality)
        if soul_text := self._read_section(AURIC_ROOT / "SOUL.md"):
            parts.append(soul_text)

        # 2. The User (Only at Depth 0)
        if task_context.depth == 0:
            if user_text := self._read_section(AURIC_ROOT / "USER.md"):
                parts.append(f"## User Context\n{user_text}")

        # 3. The Time & Environment
        parts.append(f"## Current Time\n{datetime.now().isoformat()} EST")
        parts.append(f"## Environment\nOS: {platform.system()} {platform.release()}\nCWD: {os.getcwd()}\nNote: When using `execute_powershell`, standard PowerShell syntax applies.")

        # 4 Memory & Abilities
        if memory_text := self._read_section(AURIC_ROOT / "memories" / "MEMORY.md"):
            parts.append(memory_text)

        if self.tool_registry:
            parts.append(self.tool_registry.get_spells_context())

        # 5. The Tools
        parts.append("## Available Tools\nYou have access to tools provided via native function calling. **CRITICAL: You may ONLY use the tools provided to you. Do NOT invent, guess, or hallucinate tool names that were not given to you. If a tool you want does not exist, use the tools you have to accomplish the task instead (e.g., use write_file, execute_powershell, or run_python), OR create the spell to do it using the spell_crafter spell.**\n")
        parts.append("- **memory_search**: CRITICAL: You must actively use this tool to search your Grimoire/Memories for past context, user instructions, or task status if you need them. They are NOT provided automatically. It uses semantic search to find relevant snippets. PREFER this over reading files directly when looking for information.")
        parts.append("- **read_file**: Use this only when you need to read a specific file's exact content, OR when memory_search failed to find memories.")

        # Inject Pact Tools
        if self.pact_manager:
            if pact_tools := self.pact_manager.get_all_tools_definitions():
                parts.append(pact_tools)

        # 6. The Memory (Snapshot from Semantic Search)
        if task_context.relevant_snippets:
            parts.append("## Relevant Context (Grimoire)")
            parts.extend(task_context.relevant_snippets)

        # 7. The Focus (Working Memory)
        if focus_text := self._read_section(AURIC_ROOT / "memories" / "FOCUS.md"):
            parts.append(focus_text)
        
        return "\n\n".join(parts)

    def _get_available_tool_names(self) -> list:
        """Collects all known tool names from registry, spells, pacts, and builtins."""
        names = set()
        names.add("spawn_sub_agent")
        if self.tool_registry:
            names.update(self.tool_registry._internal_tools.keys())
            names.update(self.tool_registry._spells.keys())
        if self.pact_manager:
            names.update(self.pact_manager.get_tool_names())
        return list(names)

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

    def _read_section(self, path: Path) -> Optional[str]:
        """Helper to read a markdown section if it exists."""
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Failed to read section {path}: {e}")
        return None

    def _get_recursion_tool_schema(self) -> Dict[str, Any]:
        """Returns the schema for the spawn_sub_agent tool."""
        return {
            "type": "function",
            "function": {
                "name": "spawn_sub_agent",
                "description": "Delegates a complex sub-task to a recursive sub-agent. The sub-agent has its own context and tools. Use this ONLY for tasks that genuinely require independent multi-step reasoning, or long-form content generation (e.g. breaking novel writing into chapters, breaking documentation into sections or steps, etc.). Do NOT use this for simple content generation — just produce the content directly.",
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
        }
