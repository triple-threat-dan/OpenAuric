Here is the `README.md` for the repository. It is written to be the single source of truth for your engineering team and the first touchpoint for the community.

---

# OpenAuric

> **The Recursive Agentic Warlock.**
> *Invoke the Pact. Automate the Realm.*

**OpenAuric** is a local, autonomous AI agent architecture designed for developers who need more than a chatbot. It is a **Recursive Language Model (RLM)** system that manages its own context, writes its own tools, and persists memory through a structured file system called *The Grimoire*.

Unlike standard agents that suffer from context drift, Auric anchors itself using a persistent "Focus" state and recursively spawns sub-agents to handle complex tasks without polluting the main context window. The Dream Cycle consolidates the agent's memory and ensures long-term stability.

---

## 🔮 The Philosophy

OpenAuric is built on the metaphor of the **Warlock**:

* **The User (You):** The user and administrator.
* **The Agent (Auric):** The local Python daemon running on your machine.
* **The Patron (LLM):** The raw intelligence provider (OpenAI, Anthropic, Gemini, or local Ollama), connected via LiteLLM.
* **The Grimoire (Memory):** A structured file system (`~/.auric/grimoire`) where the agent stores its Working Memory, Long-term Facts, and Self-written Scripts.

---

## ✨ Key Features (v0.1)

* **🧠 Recursive Language Model (RLM):** Auric can "spawn" sub-agents to solve specific problems (e.g., "Summarize this PDF") and return only the result to the main thread, saving tokens and maintaining coherence.
* **📚 The Grimoire:** A transparent, file-based memory system.
* `FOCUS.md`: The "Working Memory" representing the current task state. Loaded on every turn.
* `incantations/`: Python scripts the agent wrote to solve past problems.
* `chronicles/`: Episodic memories and daily summaries.


* **🛡️ The Circle (Sandbox):** A safe Python execution environment where Auric can write and run code to query its own memory or perform tasks.
* **👁️ Omni-Channel Pacts:** Connect Auric to Telegram, Discord, and GitHub.
* **💤 The Dream Cycle:** An automated consolidation process that runs when the agent sleeps, summarizing the day's logs into permanent knowledge.
* **🖥️ Textual Dashboard:** A "Matrix-style" TUI (Terminal User Interface) to visualize the agent's thought process and memory state in real-time.

---

## ⚡ Installation

OpenAuric is designed for **Linux** and **WSL2** environments.

### The Summoning (Quick Install)

```bash
# Clone the repository
git clone https://github.com/your-username/openauric.git
cd openauric

# Run the summoning script
chmod +x install.sh
./install.sh

```

This script will:

1. Check for Python 3.11+.
2. Create the `~/.auric` directory structure.
3. Install dependencies in a dedicated virtual environment.
4. Alias the `auric` command in your shell.

---

## 🕹️ Usage

### Core Commands

Manage the daemon process using the CLI:

```bash
# Awaken the agent (starts the background daemon)
auric start

# Open the TUI Dashboard (View logs, memory, and status)
auric dashboard

# Check connection status to your Patron
auric status

# Put the agent to sleep (Stops daemon & triggers Dream Cycle)
auric stop

```

### Configuration

Configure your Patron (LLM Provider) and API keys:

```bash
# Opens ~/.auric/auric.json in your default editor
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

## 📖 The Grimoire (Memory System)

Auric's intelligence relies on **The Grimoire**, located at `~/.auric/grimoire/`. You are encouraged to read and edit these files manually—Auric will see your changes instantly.

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

### `incantations/` (Skills)

Folder containing Python scripts Auric has written.

* *Example:* `incantations/docker_fix.py`

### `codex/` (Facts)

Long-term semantic memory.

* *Example:* `codex/project_specs.md`

---

## 🤝 Contributing

We are currently in **Phase v0.1 (The First Invocation)**.
See `CONTRIBUTING.md` for details on how to set up the dev environment and run tests.

### Roadmap

* [ ] More Incantations (Skills)
* [ ] More Pacts (Channels)
* [ ] Voice Interface via Whisper/TTS.
* [ ] Full GUI Web Dashboard (React).

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for more information.