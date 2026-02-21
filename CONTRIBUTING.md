# Contributing to OpenAuric

Welcome! Thank you for your interest in contributing to OpenAuric. This document outlines the standards and process for contributing to the project.

## 🧙 The Philosophy

OpenAuric is a **recursive agentic warlock** designed to be local-first and self-extending.
When contributing, please keep these core tenets in mind:

1. **Recursion over Bloat**: If a task is complex, the agent should spawn a sub-agent (RLM) rather than executing a large monolithic function. We emphasize modularity and composability, while maintaining token/context efficiency.
2. **Memory is Sacred**: The file-based memory (`.auric/memories`) is the source of truth. ChromaDB is used for semantic search and long-term memory consolidation.
3. **Tools are Spells**: The agent extends itself by writing Python scripts in `.auric/grimoire`. Core infrastructure lives in `src/`.

---

## 🛠️ Development Environment

### Prerequisites
- **Windows 10+** or **Linux** or **WSL2** (**MacOS** is not tested but theoretically should work, contact the author if you'd like to help!)
- **Python 3.12+** (Strict requirement for RAG/ChromaDB compatibility).
- **Git**   

### Installation

1. **Clone the Repository**
   ```bash
   git clone https://github.com/triple-threat-dan/openauric.git
   cd openauric
   ```

2. **Run the Setup Script**
   - **Windows (PowerShell)**: `.\scripts\install.ps1`
   - **Linux/macOS**: `./scripts/install.sh`

   *Alternatively, set up manually:*
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # or .venv\Scripts\activate
   pip install -e .
   pip install pytest
   ```

3. **Verify Installation**
   ```bash
   auric --help
   ```

---

## 🧪 Testing

We use `pytest`. Before submitting a Pull Request (PR), please ensure all tests pass.

```bash
pytest tests/
```

- **Unit Tests**: Fast tests for internal logic.
- **Integration Tests**: Slower tests involving the Database or RAG components.

---

## 📂 Project Structure

- `src/auric/`: Core daemon logic.
  - `core/`: State management (RLM, Daemon, Config).
  - `memory/`: Memory management (Librarian, Vector Store).
  - `spells/`: Tool registry and spell management.
  - `interface/`: Adapters for external platforms (Discord, Telegram).
- `.auric/`: Local runtime state (ignored by git).
  - `grimoire/`: User-specific scripts and skills.
  - `memories/`: Markdown memory files.

---

## 📜 Submitting a Pull Request

1. **Fork the Repository**.
2. **Create a Branch**: `git checkout -b feat/your-feature-name`.
3. **Commit Changes**: Keep commit messages clear and descriptive.
4. **Push to Fork**: `git push origin feat/your-feature-name`.
5. **Open a Pull Request**: Describe what you added and why.

---

## 📝 Style Guide

- **Code**: Follow PEP 8 standards.
- **Docstrings**: Required for all public methods and classes.
- **Typing**: Use standard Python type hints (`List`, `Optional`, etc.) everywhere.

---

## AI-Friendly Development

AI-generated code is welcome! I only ask that any PRs and submissions (AI or not) be fully tested and reviewed by a human before being merged. Make sure to add tests and documentation when appropriate.

*Expand the Grimoire. Automate the Realm.*
