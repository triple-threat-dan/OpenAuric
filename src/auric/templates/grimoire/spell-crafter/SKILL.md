---
name: spell-crafter
description: Guide for creating/updating spells (skills) to extend auric with specialized knowledge, workflows, or tool integrations.
---

# Spell Crafter

Guide for creating effective spells (skills).

## About Skills

Skills are modular packages extending auric with specialized workflows, tools, and domain expertise. They transform a general-purpose agent into a specialized one.

### What Skills Provide
1.  **Specialized workflows:** Multi-step domain procedures.
2.  **Tool integrations:** File format or API instructions.
3.  **Domain expertise:** Schemas, business logic.
4.  **Bundled resources:** Scripts and assets for complex/repetitive tasks.

## Core Principles

### Concise is Key
Context is scarce. **Assume auric's agent is smart.** Only add missing context. Prefer concise examples over verbose explanations.

### Degrees of Freedom
Match specificity to task fragility:
* **High (Text instructions):** Multiple valid approaches; context-dependent.
* **Medium (Pseudocode/Params):** Preferred patterns; some variation allowed.
* **Low (Scripts):** Fragile operations; strict consistency required.

### Anatomy of a Skill

```

skill-name/
├── SKILL.md (Required)
│   ├── Frontmatter (name, description)
│   └── Body (Instructions)
└── Bundled Resources (Optional)
├── scripts/    - Executable code
├── references/ - Context loaded on-demand
└── assets/     - Output files

```

#### SKILL.md (Required)
* **Frontmatter (YAML):** `name` and `description` only. Used for triggering.
* **Body (Markdown):** Instructions loaded *only after* triggering.

#### Bundled Resources (Optional)
* **Scripts (`scripts/`):** Code (Node/Python/Bash) for reliability or repetition.
    * **Agentic Ergonomics:** Must output LLM-friendly stdout, suppress tracebacks, and truncate/paginate output.
* **References (`references/`):** Schemas, policies, docs. Loaded on-demand.
    * *Best Practice:* Move details here to keep `SKILL.md` lean. Use grep patterns for files >10k words.
* **Assets (`assets/`):** Output resources (templates, logos). Not loaded into context.

**Do NOT Include:** README, INSTALLATION, CHANGELOG, etc. Only functional files.

### Progressive Disclosure
1.  **Metadata:** Always in context (~100 words).
2.  **SKILL.md Body:** On trigger (<5k words).
3.  **Resources:** On demand (Unlimited).

#### Patterns
* **References:** Link `FORMS.md` or `API.md` from `SKILL.md`.
* **Domain:** Split `sales.md`, `finance.md`.
* **Variants:** Split `aws.md`, `gcp.md`.
* **Conditional:** Link `ADVANCED.md` for specific features.
* **Rules:** Keep refs 1 level deep. Add TOC for files >100 lines.

## Skill Creation Process

1.  **Understand:** Define usage with examples.
2.  **Plan:** Identify reusable resources.
3.  **Init:** Run `init_skill.py`.
4.  **Edit:** Implement resources and `SKILL.md`.
5.  **Iterate:** Refine.

### Naming
* Lowercase, digits, hyphens only (<64 chars).
* Verb-led (e.g., `plan-mode`).
* Namespace if needed (e.g., `gh-address-comments`).
* Directory must match skill name.

### Step 1: Understand
Define functionality via concrete user examples/triggers. Bias toward action: propose features/examples and ask user to refine. Avoid interrogation loops.

### Step 2: Plan Resources
Analyze examples to identify reusable content:
* *Repetitive code* -> `scripts/`
* *Boilerplate/Templates* -> `assets/`
* *Schemas/Docs* -> `references/`

### Step 3: Initialize
Run the `init_skill.py` script provided in this spell's `scripts` directory.
You MUST use the `{spell_path}` variable to locate the script.

**Usage:**
```bash
python {spell_path}/scripts/init_skill.py '{"name": "my-new-spell"}'
```

This will automatically create the spell in `~/.auric/grimoire/spells/my-new-spell`.
Do NOT create files manually in the current directory unless explicitly requested. Always use the standard Grimoire location.

### Step 4: Edit

#### Resources

Implement `scripts/`, `references/`, `assets/`. Test scripts. Delete unused example files.

#### SKILL.md

* **Frontmatter:**
* `name`: Skill name.
* `description`: **Single-line string.** Primary trigger. Include "what" it does and "when" to use it.
* *Example:* `description: Data ingestion for tabular data. Use for CSV/TSV analysis and schema normalization.`
* `parameters_json`: **Optional JSON string.** Defines the tool parameters schema (OpenAI function format).
* *Example:* `parameters_json: {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}`


* **Body:** Imperative instructions.


Fix any reported validation errors.

#### Executable Spells
If the spell requires executing code (e.g., getting weather, processing data), in the `scripts/` subdirectory   add a script named `run.py`, `run.ps1`, or `run.sh`.

The script receives arguments as a single JSON string in the first command-line argument.
Example `run.py`:
```python
import sys, json
args = json.loads(sys.argv[1])
# ... logic ...
print("Result")
```

### Step 5: Iterate

Refine `SKILL.md` or resources based on real-world usage and failures.
