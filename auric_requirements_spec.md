Below is the comprehensive **OpenAuric v0.1 Milestone** plan.

This roadmap focuses on building a recursive AI agent that manages its own memory through code, runs safely in a sandbox, and connects to your entire digital ecosystem, inspired by Warlocks of DnD lore.

# **Project Name:** OpenAuric

**CLI Command:** `auric`
**Tagline:** *The Recursive Agentic Warlock.*

---

## **GitHub Milestone: v0.1 - "The First Invocation"**

### **Epic 1: The Vessel (Core Infrastructure & Textual UI)**
*The physical shell of the agent and how you perceive it.*


* **[Feature] The `auric` CLI (Typer + Textual):**
* Build the main entry point with `Typer`.
* Implement `auric start` (Daemonize), `auric stop` (Kill signal), and `auric restart`.



* **[Feature] The Dashboard - The Arcane Library (`auric dashboard`):**
* Build an HTML/CSS/JS frontend app that connects to the running daemon
* Implement `auric dashboard <start>` (Daemonize), `auric dashboard stop` (Kill the process), and `auric dashboard restart` (perhaps for reloading the dashboard config options from `auric.json`, for example when the port is changed).
* **Visuals:** A split-screen "Matrix"-style view.
* *Middle/Left Pane:* Chat log (communication with user/owner).
* *Right Pane:* The "Grimoire" state (current memory/context object visualization).
* *Bottom Pane:* System stats (Token usage, CPU/GPU usage for local models).
* *Sidebar Nav:* Access to sessings and other tools.
* *Settings page:* a frontend way to modify the agent's options, stored in the config file `.auric/auric.json`



**[Feature] The Heartbeat (Proactive Vigil)**
* **Concept:** A scheduled "wake-up" event that allows Auric to perform background checks without user input.
* **Mechanism:**
* The daemon runs an internal scheduler (default: every 30 minutes).
* **Active Hours Check:** Before executing, check `auric.json` (`agents.defaults.heartbeat.activeHours`). If outside active hours (e.g., 2 AM), skip the heartbeat to respect the user's peace.

* **Instruction Source (`HEARTBEAT.md`):**
* Auric reads `~/.auric/HEARTBEAT.md`.
* This file contains a checklist of routine tasks (e.g., "Check unread Gmails from 'Boss'," "Verify server status").
* *Optimization:* If `HEARTBEAT.md` is empty or missing, the heartbeat is skipped entirely to save API costs.

* **The Logic:**
1. Scheduler triggers.
2. Inject `HEARTBEAT.md` content into the System Prompt.
3. Send prompt: *"Perform your scheduled vigil. If no significant events are found, reply only with HEARTBEAT_OK."*

* **Output Handling:**
* **Response == "HEARTBEAT_OK":** The daemon logs "Health check passed" and goes back to sleep. **No message is sent to the user.**
* **Response != "HEARTBEAT_OK":** The response is treated as an **Alert** and pushed to the Dashboard/Telegram/Discord immediately.

* **Configuration (`auric.json`):**
```json
"agents": {
  "defaults": {
    "heartbeat": {
      "enabled": true,
      "interval": "30m",
      "activeHours": "09:00-18:00",
      "target": "telegram"
    }
  }
}

```



* **[Feature] The Config file**
* All settings for configuring Auric will be in the global config located at `.auric/auric.json`. Everything from LLM providers/models, API keys and secrets, to skills and anything else. 
* Getting and setting config options can also be done from the cli. FExamples: 
```
auric config get browser.executablePath
auric config set browser.executablePath "/usr/bin/google-chrome"
auric config unset tools.web.search.apiKey
```
* Values in the cli are parsed as JSON5 when possible; otherwise they are treated as strings. Use `--json` to require JSON5 parsing. Examples:
```
auric config set agents.defaults.heartbeat.every "0m"
auric config set gateway.port 19001 --json
auric config set channels.whatsapp.groups '["*"]' --json
```

* **[Infrastructure] The Install Script (`install.sh`):**
* One-line setup for WSL2/Linux.
* Installs Python 3.11, creates the hidden `~/.auric` directory, creates the `auric.json` config file, and sets up the systemd service.
* Also installs necessary files for the dashboard to work (server, frontend files, etc.) and spins up the webserver on a port defined in the config.




### **Epic 2: The Mind (RLM, The Grimoire & The Dream Cycle)**

*The cognitive architecture that allows the agent to learn, retain skills, and condense experiences into wisdom. Auric manages its own context by treating memory as an external object it can query via code.*



**[Feature] Dynamic System Prompt Assembly**
* **Concept:** The "Prompt" is not a static string; it is a dynamic construct assembled at runtime for every turn.
* **Implementation:**
* Create a `SystemPrompt` class responsible for orchestrating the assembly.
* **Assembly Order:**
1. **The Soul:** (`SOUL.md`) - The personality.
2. **The User:** (`USER.md`) - The context of who is being helped.
3. **The Time:** Current system time and date (critical for scheduling).
4. **The Grimoire (Focus):** (`FOCUS.md`) - The immediate task state.
5. **The Tools:** JSON schemas of available MCP tools and local skills.
6. **The Memory:** Relevant excerpts fetched from the Codex or Chronicles (if a recall triggered).

* **Dynamic Context Injection:** The builder must intelligently truncate older messages or lower-priority context if the token limit is approaching, prioritizing the `FOCUS.md` and `SOUL.md` above all else.



**[Feature] The User's Profile (`USER.md`)**
* **Concept:** A static file containing immutable facts about the user.
* **File:** Located at `~/.auric/USER.md`.
* **Content:** Name, pronouns, timezone/location, preferred coding languages, and bio.
```markdown
# USER.md - About Your Human

*Learn about the person you're helping. Update this as you go.*

- **Name:** Daniel
- **What to call them:** Dan
- **Pronouns:** He/Him
- **Timezone:** Eastern Time (North Carolina, UTC-5/UTC-4)
- **Notes:** Creator of Aliss the AI agent

## Context

- Profession: C# .NET software engineer with AI research interest
- Family: wife & kids

---

The more you know, the better you can help. But remember — you're learning about a person, not building a dossier. Respect the difference.
```
* **Token Optimization (Crucial):**
* **Root Agent:** Receives `USER.md` in full to maintain a personalized relationship.
* **Sub-Agents (RLM):** Do **NOT** receive `USER.md`. Sub-agents are ephemeral task-runners (e.g., "Parse this JSON"); they do not need to know the user's name or pronouns. This saves significant tokens during recursive operations.


* **[Feature] The Grimoire (Hierarchical Neural File System):**
* Instead of a simple database, we have a structured, file-centric knowledge base located at `~/.auric/grimoire/`.
* **Structure:** The Grimoire is divided into three distinct "Planes" of memory:
1. **The Focus (Scratchpad):** `FOCUS.md`. The agent's "Working Memory." Stores the immediate goal, current step, and active context. *Read/Write: Constant.*
2. **The Chronicle (Episodic):** `grimoire/chronicles/YYYY-MM-DD.md`. Summarized narratives of past events (not raw logs). *Read: Occasional. Write: Batch.*
3. **The Codex (Semantic):** `grimoire/codex/`. Markdown files containing hard facts and user preferences (e.g., `MEMORY.md`, `project_bravo_facts.md`). *Read: Frequent. Write: Selective.*
4. **The Incantations (Procedural):** `grimoire/incantations/`. Executable Python scripts and "How-To" guides the agent has written for itself to solve recurring problems. *Read: On-demand.*
5. **The Logs:** The raw chat logs so that the UI can display them. These are separate from The Grimoire and the LLM does not use them directly, and are stored in a separate directory from the Grimoire, because it would be inefficient for the LLM to have acceess to them and defeat the purpose of **The Grimoire**. 



* **[Feature] The Ritual of Recall (Recursive Context Retrieval):**
* **The Problem:** Preventing context window overflow and high token usage while maintaining high coherence.
* **The Workflow:** Aside from the current task state (the Working Memory), most memory is not "automatic"; it is an action. And before answering a user/generating a response, Auric does not just "look back." Instead, Auric constructs its context window dynamically:
1. **Always Invoked:** The content of **The Focus** (Current State - kept in `FOCUS.md`) is always loaded into the top LLM's context window, to keep it on-task.
2. **Vector Search:** Perform a semantic search against **The Codex** (Facts) and **The Incantations** (Skills) to pull relevant constraints or tools.
3.  **Temporal Lookup:** Only access **The Chronicle** (episodic memory) if the user explicitly references a past time (e.g., "What did we do last Tuesday?").
* **Implementation:** Use a lightweight local vector index like `SQLite-vec` to tag the `codex` and `incantations` files for fast retrieval.
* **Example:**
1. **Assess:** Auric checks its **Passive Context** (Focus + System Prompt).
2. **Reason:** If information is missing, Auric writes a script using the Grimoire API.
3. **Execute:** The script runs in the sandbox (e.g., `grimoire.knowledge.search("deploy errors")`).
4. **Inject:** The output of the script (not the whole file) is added to the active context window.
5. **Call tools or Sub-Agents:** With the context object populated, the LLM might recursively call a sub-agent instance, passing instructions and the context window to it, then get its output and continue in a REPL loop.


**[Feature] The Focus (Current Task/Working Memory)**
* **The Problem:** Preventing Context Drift and hallucination.
* **Solution:** The state of the current task is tracked and kept in context at all times, as well as saved to the FOCUS.md file.
* **The Implementation: `FOCUS.md`** - This file acts as the "Task Anchor". It is loaded into the **System Prompt** on *every single turn*. Details below.

#### 1. The Structure (Markdown Checklist)

Don't just store text; store *state*. Use a Markdown structure that the LLM can edit.

* **File Content: `~/.auric/grimoire/FOCUS.md`:**

```markdown
# 🔮 THE FOCUS (Current State)

## 🎯 Prime Directive (The "Why")
User asked to: "Scrape the latest stock prices and save to CSV."

## 📋 Plan of Action (The "How")
- [x] Create project folder `stock_scraper`
- [x] Install `beautifulsoup4` and `requests`
- [x] Write `scraper.py` script
- [ ] Run script and verify CSV output <--- CURRENT STEP
- [ ] Notify user of completion

## 🧠 Working Memory (Scratchpad)
- Encountered 403 Forbidden error on previous run.
- Attempting to add 'User-Agent' headers to fix it.

```

#### 2. The Lifecycle (How it works in code): 
*Enforce a strict lifecycle so the agent doesn't get stuck in a loop.*

1. **Phase A: Initialization (The Hook):** When the user gives a new high-level prompt, the Agent *overwrites* `FOCUS.md`.
   * **Agent Logic:** "New request received. Initializing Focus. Breaking request down into 5 steps."
2. **Phase B: Iteration (The Loop):** Before every tool call (e.g., running python), the Agent *must* update the Focus.
   * **Agent Logic:** "I finished step 2. Checking the box. Moving cursor to Step 3. Updating 'Working Memory' with the error I just saw."
   * *Crucial:* This allows you (the human) to open the file, see it's about to do something stupid, and edit the file manually. The agent will read your edit on the next turn and change course.
3. **Phase C: Completion (The Pop):** When the task is done, we don't just delete it. we **"Pop to History,"** then update the execution logs in the db.
   1. **Read** the completed `FOCUS.md`.
   2. **Summarize** it into a single line: *"Successfully scraped stock prices after fixing 403 error."*
   3. **Append** that line to `chronicles/YYYY-MM-DD.md`.
   4. **Clear** `FOCUS.md` (or reset it to "Idle / Awaiting Command").
   5. **Update** the `task_executions` and `task_step_executions` tables in the db accordingly
* **Note:** If the agent tries a step, fails, updates FOCUS.md with the error, tries again, fails, and updates again... it might get stuck. Add a "Retry Counter" to the `FOCUS.md` schema. If a specific checkbox has >3 failures, the FocusManager should force a "Plan Revision" or ask the User for help.

#### 3. Python Implementation Strategy

In the `Auric` class, we can have a `FocusManager` helper class to manage this easily, with `read()`, `update(new_plan, current_step_notes)`, and `clear()` methods.


#### 4. Why this is better than just "Memory"

1. **Self-Correction:** If the agent hallucinates and thinks it's already finished the code, it looks at `FOCUS.md` and sees `[ ] Write script` is unchecked. It forces the agent to be honest.
2. **Resumability:** If the process crashes or you restart the `auric` daemon, it reads `FOCUS.md` on boot and picks up exactly where it left off.
3. **Token Efficiency:** You don't need to feed it the last 50 conversation turns to remind it what it's doing. You only need the last ~5 turns + `FOCUS.md`.




* **[Feature] The Dream Cycle:**
* **The Concept:** A background consolidation process that runs when the agent is idle, once per day during a sleep cycle, or upon session termination (`auric stop`).
* **The "Dream" Logic:**
1. **Ingest:** Reads the raw, massive `daily_log.md` of the current session.
2. **Distill (Semantic):** LLM extracts permanent truths ("User moved to a new API key") and updates the relevant file in **The Codex**.
3. **Scribe (Procedural):** LLM identifies successful code patterns used today and saves them as reusable scripts in **The Incantations**.
4. **Compress (Episodic):** LLM summarizes the day's events into a short narrative paragraph and appends it to **The Chronicle**.
5. **Forget:** The raw `daily_log` is archived (zipped) and removed from the active context search path to prevent noise. The raw chat logs are still accessed by the dashboard UI for the purposes of displaying them, but it does not send them to the LLM.



* **[Feature] The Recursive Language Model (RLM)**
* **Purpose:** To give the Top-level LLM the ability to call either itself or other specialized LLMs as sub-agents, recursively, while only passing the context object and specific instructions, to limit context token usage and allow specialized models to handle specific tasks.
* **The Workflow:** 
1. The main LLM agent is an RLM. After defining the current task, the main/top-level LLM takes the user's qeury and uses that to write some instructions and recursively call a sub-agent (either itself or a different specialized model), passing the context object (the Grimoire) to a dedicate python REPL environment along with these instructions as a prompt.
2. The Sub-Agent (or root LM, depth=0) has access to the python REPL environment containing the Grimoire/Context object and carries out a specific sub-task for the main LLM/RLM. The root LM has the ability to call a recursive LM (or LM with depth=1) inside the REPL environment as if it were a function in code, allowing it to naturally peek at, partition, grep through, and launch recursive sub-queries over the context. The Sub-LM model is chosen based on task domain and complexity, for example simple tasks could be handled by smaller general purpose models while a coding task might be delegated to a coding model. These sub-LMs could not only generate python code to query the Context/Grimoire, but they could also generate python code to get a specific output (calculate digits of Pi, etc.), or generate a chapter of a novel, output the code for a single function, write a script to call an external tool, and so on.
3. When the root LM is confident it has an answer, it can either directly output the answer as FINAL(answer), or it can build up an answer using the variables in its own REPL environment, and return the string inside that answer as FINAL_VAR(final_ans_var)
4. The main/parent LLM (RLM) gets the result/response from the sub-LM and moves on to the next step of the task, continuing until all steps are complete.
* **The Result:** The script output or sub-LM outputs are injected into the current context window. This creates an effectively infinite memory by "paging in" only what is needed.
* **The Context Object:** The context can, in theory, be any modality that can be loaded into memory. The root LM has full control to view and transform this data, as well as ask sub-queries to a recursive LM.
* **Safeguards:** Include a "Max Recursion Depth" (Depth=2). We don't want the RLM to spawn a sub-agent, which spawns a sub-agent, ad infinitum, draining your API credits in seconds. Keep the recursion depth minimal.


* **[Feature] Task Executions (Database Logs)**
* **Purpose:** To store each executed Task in a `task_executions` table of the local sqlite database for logging and reviewing what the LLM agent did.
* This is purely for debugging and history and is not accessed by the LLM.
* The code creates a new `task_execution` entry in the database when the agent begins a new task.
* As each step of the task is completed, tool calls are made, memory is updated, sub LLMs are recursively called, and responses are generated, a `task_step_execuition` entry is saved in the db as well, which foreign keys to the `task_executions` table.
* This way the user can look at a Task History page in the dashboard ot see a history of tasks performed and their execution steps/overall outcomes.
* Errors that get thrown during the task are also logged in the task_executions table. This allows the user to debug why an LLM failed in its task.




* **[Feature] The Patron Interface:**
* Abstract the LLM provider to support Gemini, OpenAI, Anthropic, OpenRouter, or Local/Ollama models interchangeably.
* **Dual-Mode:** Support a "Fast Model" (e.g., Gemini Flash/GPT-4o-mini) for the *Ritual of Attunement* and a "Smart Model" (e.g., Gemini Pro/Claude 3.5 Sonnet) for *The Dream Cycle* consolidation tasks.



---

#### **The "Dream Cycle" Workflow (Visualized)**

* **Purpose:** To mimic the human brain's nightly cleanup during sleep, as long memories clutter the context and storage.
* **Workflow**: When you finish a long chat and the bot is idle for some time, or once per day at a set time while you sleep, or when manually triggered using `auric dream`:

1. **Auric (Internal Monologue):** "I must meditate on what occurred previously."
2. **Action (The Dream Cycle):** Auric reads the 500-line chat log from today.
3. **Extraction 1 (Skill):** It notices you successfully fixed a Docker bug. It writes `incantations/docker_fix.py`.
4. **Extraction 2 (Fact):** It notices you mentioned your AWS region is `us-east-1`. It updates `codex/infrastructure.md`.
5. **Compression:** It writes to `chronicles/2026-02-03.md`: *"User struggled with Docker networking. We solved it by exposing port 8080. AWS region confirmed as us-east-1."*
6. **Result:** Next time you ask "How do I run the container?", Auric doesn't read the chat log; it simply reads `incantations/docker_fix.py`.
* **Safeguards:** The dream cycle should be skipped if there is nothing to summarize/nothing has happened since the last dream cycle, to prevent unnecessary token usage.



---

### **Epic 3: The Circle (Sandboxed Execution)**

*The safety mechanism that allows the agent to cast spells without destroying the castle.*

* **[Feature] The Python Sandbox:**
* Create a dedicated, isolated Python environment (separate from the system python) for the agent to run its "Recall" scripts and task scripts - anything the agent determines it needs python for.
* **Constraint:** The agent can `pip install` new packages, but only with permission. It works with a pre-defined set of "safe" libraries (Pandas, Numpy, Requests).


* **[Feature] Tool: `run_python`:**
* The core function the LLM calls to execute code inside the Circle.
* Capture `STDOUT` and `STDERR` and feed it back to the Patron as "Sensory Data."



---

### **Epic 4: Pacts and Feats (Omni-Channel Interfaces)**

*The "Scrying Pools" that allow Auric to see and speak across platforms.*

* **[Feature] The Gateway Manager (Adapter Pattern):**
* A plugin system to manage incoming/outgoing message streams so Auric can exist on multiple platforms simultaneously and the Auric AI can call external tools.


* **[Feature] Feats: The Developer's Toolkit (GitHub & Gemini-CLI):**
* **Feats** are specific skills and tool calls the agent can perform. These need to be dynamic and extensible so in the future the LLM can acquire skills for itself. If the Auric agent is asked to hook up to a user's GitHub and push a change/commit, the Agent should acquire this skill/feat and then do it. If the user asks the agent to brows the web and perform certain actions, Auric should acquire this Skill/Feat and do it. If it lacks a skill or feat, it should be able to acquire it/by implementing it.
* **GitHub:** Integration via PyGithub. Allow Auric to read Issues, check PR diffs, push changes, and comment.
* **Gemini-CLI:** Wrapper to invoke Google's CLI tools for advanced reasoning tasks if configured.
* And so on...


* **[Feature] Pacts: External Integrations for Communication (Discord, Slack, Telegram, WhatsApp, Google):**
* **Unified Message Bus:** Normalize messages from all these platforms into a single "Event" object.
* **Live chat:** Gives the Agent an external chat interface with the ability to share and receive messages, files, etc. Basically keeps the user from being constrained to only one local chat interface, allowing them to chat with their agent from anywhere.
* *Implementation Note:* For v0.1, implement **Discord** and **Telegram** fully to prove the architecture, with stubs for Slack/WhatsApp/Claude Code.


---

### **Epic 5: The Hand (System Agency)**

* **[Feature] Tool: `run_shell`:**
* Allow execution of bash commands (git, grep, ls). 
* **Safety:** "Human-in-the-loop" requirement for high-risk commands (`rm`, `dd`, `chmod`).



---

## **The "Recursive Recall" Workflow (Visualized)**

When you ask `auric`: *"Why did the build fail last Tuesday?"*

1. **Auric (Internal Monologue):** "I need to check the history for 'build fail' and 'Tuesday'."
2. **Action:** Auric writes a script:
```python
import grimoire
results = grimoire.query(
    date="last Tuesday", 
    keywords=["build", "error", "fail"]
)
print(results)

```
3. **Execution:** The script runs in the Sandbox.
4. **Observation:** The script prints: *"Found 2 logs: 'NuGet restore timeout' and 'Database locked'."*
5. **Response:** Auric replies to you: *"It looks like you had a NuGet timeout and a database lock issue last Tuesday."*
