

<p align="center">
  <img src="ALISS_AURIC.png" alt="auric, the recursive agentic warlock">
  <h1>OpenAuric: The Recursive Agentic Warlock</h1>
  <p>
    <a href="https://github.com/triple-threat-dan/OpenAuric/actions/workflows/tests.yaml"><img src="https://github.com/triple-threat-dan/OpenAuric/actions/workflows/tests.yaml/badge.svg" alt="Python Tests (uv)"></a>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</div>

> *Invoke the Pact. Automate the Realm.*.

**OpenAuric** is a lightweight, local, autonomous AI agent architecture designed for developers who need more than a chatbot. It is a **Recursive Language Model (RLM)** system that manages its own context, writes its own tools in *The Grimoire*, and persists memory through a structured file system.

Unlike standard agents that suffer from context drift, Auric anchors itself using a persistent "Focus" state and recursively spawns sub-agents to handle complex tasks without polluting the main context window. The Dream Cycle consolidates the agent's memory and ensures long-term stability and better context management.

---

## 🔮 The Philosophy

OpenAuric is built on the metaphor of the **Warlock**:

* **The User (You):** The user and administrator.
* **The Agent (Auric):** The local Python daemon running on your machine.
* **The Patron (LLM):** The raw intelligence provider (OpenAI, Anthropic, Gemini, or local Ollama), connected via LiteLLM, empowering the agent.
* **The Grimoire (Skill Library):** A structured file system (`.auric/grimoire`) where the agent accesses its tools and scripts. Compatible with OpenClaw, gemini-cli, and claude code skills (any `SKILL.md` based system).
* **The Memories:** A markdown-based memory system (`.auric/memories`) where the agent stores its Working Memory (`FOCUS.md`), Long-term Facts, and self-written notes (and a ChromaDB vector store for semantic search).

---

## ✨ Key Features (v0.1)

* **🧠 Recursive Language Model (RLM):** Auric can "spawn" sub-agents to solve specific problems (e.g., "Summarize this PDF") and return only the result to the main thread, saving tokens and maintaining coherence.
* **📜 The Grimoire:** The collection of scripts Auric has written to solve problems - fully compatible with OpenClaw, gemini-cli, and claude code skills.
* `grimoire/`: Python scripts (Spells) the agent wrote or possesses to solve problems.
* **📚 The Memory:** A transparent, file-based memory system.
* `memories/FOCUS.md`: The "Working Memory" representing the current task state & steps. Loaded on every turn, managed by the agent.
* `memories/MEMORY.md`: Long-term semantic memory.


* **🛡️ The Circle (Sandbox):** A safe Python execution environment where Auric can write and run code to query its own memory or perform tasks.
* **👁️ Omni-Channel Pacts:** Connect Auric to Telegram, Discord, and GitHub.
* **💤 The Dream Cycle:** An automated consolidation process that runs when the agent sleeps, summarizing the day's logs into permanent knowledge.
* **🧠 The Patrons (LLM Providers):** Connect Auric to OpenAI, Anthropic, Gemini, or local Ollama via LiteLLM.
* **❤️ Heartbeat:** A simple heartbeat system to allow Auric to perform recurring tasks or reminders.
* **🖥️ Frontend UI:** A web UI to chat, check logs, and visualize the agent's thought process and memory state in real-time.

---

## ⚡ Installation

OpenAuric is designed for **Windows**, **Linux** and **WSL2** environments, but should run anywhere python 3.11+ is installed.

### The Summoning (Quick Install)

```bash
# Clone the repository
git clone https://github.com/triple-threat-dan/openauric.git
cd openauric

# Run the summoning script
chmod +x install.sh
./install.sh

```

This script will:

1. Check for Python 3.11+.
2. Create the `.auric` directory structure in the project root.
3. Install dependencies in a dedicated virtual environment.
4. Alias the `auric` command in your shell.

## 🚀 Quick Start

1. **Awaken the Daemon**: Start the agent in the background.
   ```bash
   auric start
   ```
2. **Set your API Key**: (e.g., for Gemini)
   ```bash
   auric config set keys.gemini YOUR_API_KEY
   ```
3. **Invoke the Warlock**: Send your first command directly from the terminal.
   ```bash
   auric -m "Who are you?"
   ```
4. **Open the Dashboard**: Access the web UI to visualize thoughts and memory.
   ```bash
   auric dashboard
   ```

---

## 🕹️ Usage

### Core Commands

Manage the daemon and interact with the agent using the CLI:

```bash
# Start the background daemon
auric start

# Send a message and wait for a response (Shortcut)
auric -m "Hello Auric!"

# Send a message using the explicit command
auric message "What's on my schedule?"

# Stop the daemon (triggers Dream Cycle)
auric stop

# Restart the daemon
auric restart
```

### Configuration

Configure your Patron (LLM Provider) and API keys:

```bash
# Opens .auric/auric.json in your default editor
auric config

```

Example `auric.json` snippet:

```json
{
  "patron": {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "sk-..."
  },
  "safety": {
    "human_confirmation_required": ["rm", "dd", "sudo"]
  }
}

```

---

## 📖 The Grimoire (Spell/Skill System)

Auric's abilities rely on **The Grimoire**, the collection of Spells located at `.auric/grimoire/`

### `grimoire/` (Skills)

Folder containing SKILL.md files and Python scripts Auric has written.

* *Example:* `grimoire/docker_fix.py`

## 📚 The Memory (Long-term Memory System)

Auric's short- and long-term memory is stored in the **Memories** at `.auric/memories/`. You are encouraged to read and edit these files manually—Auric will see your changes instantly.

### `FOCUS.md` (The Scratchpad)

The most important file. It tracks the **Current Task**. If Auric gets stuck, open this file and edit the checklist manually to guide it.

```markdown
# 🎯 Prime Directive
User asked: "Scrape stock prices."

# 📋 Plan
- [x] Write scraper script
- [ ] Run script <-- CURRENT STATE
- [ ] Save to CSV

```

### `memories/` (Context)

Long-term semantic memory (`MEMORY.md`) and current focus (`FOCUS.md`).

* *Example:* `.auric/memories/MEMORY.md`


### ChromaDB

Auric uses ChromaDB to store its long-term memory and perform hybrid search. The database is located at `.auric/chroma_db`

---

## 🤝 Contributing

We are currently in **Phase v0.1 (The First Invocation)**.
See `CONTRIBUTING.md` for details on how to set up the dev environment and run tests.

### Roadmap

* [ ] More Spells (Skills)
* [ ] More Pacts (Channels)
* [ ] Voice Interface via Whisper/TTS.
* [ ] Support for Knowledge graphs
* [ ] And much more!

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for more information.