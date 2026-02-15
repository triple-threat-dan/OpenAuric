import os
import signal
import sys
import atexit
import psutil
import typer
import json5
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Optional
from rich.console import Console
from rich.table import Table

# Shared modules
from auric.core.config import load_config, AuricConfig, ConfigLoader, AURIC_ROOT
from auric.spells.tool_registry import ToolRegistry

app = typer.Typer(help="OpenAuric: The Recursive Agentic Warlock")
dashboard_app = typer.Typer(help="Manage the OpenAuric dashboard")
config_app = typer.Typer(help="Manage OpenAuric configuration")
spells_app = typer.Typer(help="Manage Spells (Skills)")

app.add_typer(dashboard_app, name="dashboard")
app.add_typer(config_app, name="config")
app.add_typer(spells_app, name="spells")

pairing_app = typer.Typer(help="Manage Device/User Pairing")
app.add_typer(pairing_app, name="pairing")

console = Console()

PID_FILE = AURIC_ROOT / "auric.pid"

# --- Daemon Commands ---

@app.command()
def start():
    """Start the Auric Daemon with TUI."""
    import asyncio
    from auric.core.daemon import run_daemon
    from fastapi import FastAPI
    
    # --- PID File Logic ---
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if psutil.pid_exists(old_pid):
                console.print(f"[bold red]Daemon potentially already running (PID {old_pid}).[/bold red]")
                console.print(f"Run 'auric stop' or delete {PID_FILE} if this is an error.")
                raise typer.Exit(1)
            else:
                console.print(f"[yellow]Found stale PID file ({old_pid}). Overwriting...[/yellow]")
        except ValueError:
             console.print("[yellow]Invalid PID file. Overwriting...[/yellow]")

    current_pid = os.getpid()
    # Ensure directory exists
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(current_pid))
    
    def cleanup_pid():
        if PID_FILE.exists():
            PID_FILE.unlink()
            
    atexit.register(cleanup_pid)
    # ----------------------
    
    console.print(f"[green]Starting Auric Daemon (PID {current_pid})...[/green]")
    
    # Initialize FastAPI (TUI is initialized inside run_daemon if None)
    api_app = FastAPI(title="OpenAuric API")
    
    try:
        asyncio.run(run_daemon(tui_app=None, api_app=api_app))
    except KeyboardInterrupt:
        pass # Clean exit handled by finally/atexit
    except Exception as e:
        console.print(f"[bold red]Fatal Error: {e}[/bold red]")
    finally:
        cleanup_pid()

@app.command()
def stop(force: bool = typer.Option(False, "--force", "-f", help="Force kill the process")):
    """Stop the Auric Daemon."""
    if not PID_FILE.exists():
        console.print("[yellow]No PID file found. Is the daemon running?[/yellow]")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        console.print("[red]Invalid PID file content. Removing...[/red]")
        PID_FILE.unlink()
        return

    if not psutil.pid_exists(pid):
        console.print(f"[yellow]Process {pid} not found. Removing stale PID file...[/yellow]")
        PID_FILE.unlink()
        return

    console.print(f"[yellow]Stopping Auric Daemon (PID {pid})...[/yellow]")
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        
        try:
            proc.wait(timeout=5)
            console.print("[green]Daemon stopped successfully.[/green]")
        except psutil.TimeoutExpired:
            if force:
                console.print("[red]Process unresponsive. Force killing...[/red]")
                proc.kill()
                console.print("[green]Daemon killed.[/green]")
            else:
                console.print("[red]Process timed out. Use --force to kill.[/red]")
                raise typer.Exit(1)
                
    except psutil.NoSuchProcess:
        console.print("[yellow]Process already gone.[/yellow]")
    except psutil.AccessDenied:
        console.print("[bold red]Access Denied: Cannot stop process.[/bold red]")
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()

@app.command()
def heartbeat():
    """Triggers a manual system heartbeat."""
    import asyncio
    from auric.core.database import AuditLogger
    
    async def run_manual_beat():
        logger = AuditLogger()
        # No init_db needed if just logging, but safer to init
        # actually logging might fail if table missing.
        console.print("[yellow]Initializing Database...[/yellow]")
        await logger.init_db()
        
        console.print("[green]Logging Heartbeat...[/green]")
        # log_heartbeat signature: status="ALIVE", meta={}
        await logger.log_heartbeat(status="MANUAL", meta={"source": "cli"})
        console.print("[bold green]Success! Heartbeat logged.[/bold green]")

    asyncio.run(run_manual_beat())

@app.command()
def restart():
    """Restart the Auric Daemon."""
    stop()
    start()

token_app = typer.Typer(help="Manage Web UI Security Token")
app.add_typer(token_app, name="token")

@token_app.callback(invoke_without_command=True)
def token_main(ctx: typer.Context):
    """
    Get the current Web UI Security Token.
    """
    if ctx.invoked_subcommand is None:
        token_get()

def token_get():
    """Helper to get token."""
    config = load_config()
    current_token = config.gateway.web_ui_token
    
    if current_token:
        console.print(f"[green]Current Web UI Token:[/green]")
        console.print(f"[bold cyan]{current_token}[/bold cyan]")
        console.print("\nCopy this token and paste it into the Web UI when prompted.")
    else:
        console.print("[yellow]No token found. Generating one...[/yellow]")
        token_new()

@token_app.command("new")
def token_new():
    """
    Generate a NEW Web UI Security Token (Invalidates old one).
    """
    import secrets
    config = load_config()
    
    new_token = secrets.token_urlsafe(32)
    config.gateway.web_ui_token = new_token
    ConfigLoader.save(config)
    
    console.print(f"[green]Generated new Web UI Token:[/green]")
    console.print(f"[bold cyan]{new_token}[/bold cyan]")
    console.print("[yellow]Note: You will need to re-login on the Web UI.[/yellow]")
    console.print("\nCopy this token and paste it into the Web UI when prompted.")

# --- Spells Commands ---

@spells_app.command("list")
def spells_list():
    """List available spells in the Grimoire."""
    try:
        config = load_config()
        registry = ToolRegistry(config)
        
        spells = registry._spells
        if not spells:
            console.print("[yellow]No spells found in Grimoire.[/yellow]")
            return
            
        console.print(f"[bold green]Found {len(spells)} spells:[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Name")
        table.add_column("Type")
        table.add_column("Description")
        
        for name, data in spells.items():
            spell_type = "Executable" if data["script"] else "Instruction"
            table.add_row(name, spell_type, data["description"])
            
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error listing spells: {e}[/red]")

@spells_app.command("create")
def spells_create(name: str):
    """Create a new spell scaffold."""
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        console.print("[red]Invalid spell name. Use alphanumeric, hyphens, or underscores.[/red]")
        raise typer.Exit(1)
        
    spells_dir = Path("~/.auric/grimoire/spells").expanduser()
    spell_path = spells_dir / name
    
    if spell_path.exists():
        console.print(f"[red]Spell '{name}' already exists.[/red]")
        raise typer.Exit(1)
        
    try:
        spell_path.mkdir(parents=True, exist_ok=True)
        
        # Create SKILL.md
        skill_md = f"""---
name: {name}
description: TODO: Complete and informative explanation of what the skill does and when to use it. Include WHEN to use this skill - specific scenarios, file types, or tasks that trigger it.
---

# {name}

## Overview

[TODO: 1-2 sentences explaining what this skill enables]

## Structuring This Skill

[TODO: Choose the structure that best fits this skill's purpose. Common patterns:

**1. Workflow-Based** (best for sequential processes)
- Works well when there are clear step-by-step procedures
- Example: CSV-Processor skill with "Workflow Decision Tree" → "Ingestion" → "Cleaning" → "Analysis"
- Structure: ## Overview → ## Workflow Decision Tree → ## Step 1 → ## Step 2...

**2. Task-Based** (best for tool collections)
- Works well when the skill offers different operations/capabilities
- Example: PDF skill with "Quick Start" → "Merge PDFs" → "Split PDFs" → "Extract Text"
- Structure: ## Overview → ## Quick Start → ## Task Category 1 → ## Task Category 2...

**3. Reference/Guidelines** (best for standards or specifications)
- Works well for brand guidelines, coding standards, or requirements
- Example: Brand styling with "Brand Guidelines" → "Colors" → "Typography" → "Features"
- Structure: ## Overview → ## Guidelines → ## Specifications → ## Usage...

**4. Capabilities-Based** (best for integrated systems)
- Works well when the skill provides multiple interrelated features
- Example: Product Management with "Core Capabilities" → numbered capability list
- Structure: ## Overview → ## Core Capabilities → ### 1. Feature → ### 2. Feature...

Patterns can be mixed and matched as needed. Most skills combine patterns (e.g., start with task-based, add workflow for complex operations).

Delete this entire "Structuring This Skill" section when done - it's just guidance.]

## [TODO: Replace with the first main section based on chosen structure]

[TODO: Add content here. See examples in existing skills:
- Code samples for technical skills
- Decision trees for complex workflows
- Concrete examples with realistic user requests
- References to scripts/templates/references as needed]

## Resources

This skill includes example resource directories that demonstrate how to organize different types of bundled resources:

### scripts/
Executable code that can be run directly to perform specific operations.

**Examples from other skills:**
- PDF skill: extract_form_field_info.py, fill_fillable_fields.py - utilities for PDF manipulation
- CSV skill: normalize_schema.py, merge_datasets.py - utilities for tabular data manipulation

**Appropriate for:** python scripts (.py), shell scripts (.sh), powershell scripts (.ps1), or any executable code that performs automation, data processing, or specific operations.

**Note:** Scripts may be executed without loading into context, but can still be read by Gemini CLI for patching or environment adjustments.

### references/
Documentation and reference material intended to be loaded into context to inform Gemini CLI's process and thinking.

**Examples from other skills:**
- Product management: communication.md, context_building.md - detailed workflow guides
- BigQuery: API reference documentation and query examples
- Finance: Schema documentation, company policies

**Appropriate for:** In-depth documentation, API references, database schemas, comprehensive guides, or any detailed information that Gemini CLI should reference while working.

### assets/
Files not intended to be loaded into context, but rather used within the output Gemini CLI produces.

**Examples from other skills:**
- Brand styling: PowerPoint template files (.pptx), logo files
- Frontend builder: HTML/React boilerplate project directories
- Typography: Font files (.ttf, .woff2)

**Appropriate for:** Templates, boilerplate code, document templates, images, icons, fonts, or any files meant to be copied or used in the final output.

---

**Any unneeded directories can be deleted.** Not every skill requires all three types of resources.
"""
        (spell_path / "SKILL.md").write_text(skill_md, encoding="utf-8")
        
        console.print(f"[green]Created spell scaffolds at {spell_path}[/green]")
        console.print(f"Edit {spell_path}/SKILL.md to define your spell.")
        console.print("To verify, run: auric spells reload")
        
    except Exception as e:
        console.print(f"[red]Failed to create spell: {e}[/red]")

@spells_app.command("reload")
def spells_reload():
    """Reload spells in the running Daemon (and local index)."""
    # 1. Update local index
    try:
        config = load_config()
        registry = ToolRegistry(config) # Re-scans and updates SPELLS.md
        console.print(f"[green]Local index updated. Found {len(registry._spells)} spells.[/green]")
    except Exception as e:
        console.print(f"[yellow]Warning: Could not update local index: {e}[/yellow]")
        config = None # needed for port lookup if fail
    
    # 2. Notify Daemon
    try:
        # If local update failed, we might not have config loaded. Try loading again or use defaults?
        # If load_config failed, likely file permission or syntax error. Daemon might also be broken.
        if not config:
             config = load_config()
             
        host = config.gateway.host
        port = config.gateway.port
        
        # Ensure we use 127.0.0.1 for local reloading to avoid localhost resolution issues
        target_host = "127.0.0.1" if host in ("0.0.0.0", "localhost") else host
        url = f"http://{target_host}:{port}/spells/reload"
        
        console.print(f"[dim]Contacting {url}...[/dim]")
        
        # Using urllib to avoid requests dependency
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
             if 200 <= response.status < 300:
                 console.print("[bold green]Daemon reloaded spells successfully.[/bold green]")
             else:
                 console.print(f"[red]Daemon returned error: {response.status}[/red]")
                 
    except urllib.error.URLError as e:
         console.print(f"[yellow]Daemon not running or unreachable ({e.reason}). Spells will be loaded next time it starts.[/yellow]")
    except Exception as e:
         console.print(f"[red]Error contacting daemon: {e}[/red]")

# --- Dashboard Commands ---

@dashboard_app.callback(invoke_without_command=True)
def dashboard_main(ctx: typer.Context):
    """Manage the dashboard. Defaults to start if no command provided."""
    if ctx.invoked_subcommand is None:
        dashboard_start()

@dashboard_app.command("start")
def dashboard_start():
    """Start the dashboard UI."""
    import webbrowser
    config = load_config()
    host = config.gateway.host
    port = config.gateway.port
    
    url = f"http://{host}:{port}"
    console.print(f"[green]Opening Dashboard at {url}...[/green]")
    webbrowser.open(url)

@dashboard_app.command("stop")
def dashboard_stop():
    """Stop the dashboard UI."""
    console.print("[yellow]Stopping Dashboard...[/yellow]")

# --- Config Commands ---

@config_app.command("get")
def config_get(key: str):
    """Get a configuration value."""
    config = load_config()
    data = config.model_dump(mode='json')
    
    # Helper to traverse
    def get_val(d, k):
        keys = k.split('.')
        curr = d
        for subk in keys:
            if isinstance(curr, dict) and subk in curr:
                curr = curr[subk]
            else:
                return None
        return curr

    val = get_val(data, key)
    if val is not None:
        if isinstance(val, (dict, list)):
             console.print_json(data=val)
        else:
             console.print(str(val))
    else:
        console.print(f"[red]Key '{key}' not found.[/red]")

@config_app.command("set")
def config_set(key: str, value: str, is_json: bool = typer.Option(False, "--json", help="Parse value as JSON5")):
    """Set a configuration value."""
    config = load_config()
    data = config.model_dump(mode='json')
    
    # Parse Value
    parsed_value = value
    if is_json:
        try:
            parsed_value = json5.loads(value)
        except Exception as e:
             console.print(f"[red]Invalid JSON5 value: {e}[/red]")
             raise typer.Exit(code=1)
    else:
        # Infer type
        # Check for bool string
        if isinstance(value, str):
            if value.lower() == "true":
                parsed_value = True
            elif value.lower() == "false":
                parsed_value = False
            else:
                try:
                    parsed_value = int(value)
                except ValueError:
                    try:
                        parsed_value = float(value)
                    except ValueError:
                        parsed_value = value

    # Set Value
    keys = key.split('.')
    curr = data
    # Create path if missing?
    # No, we assume structure exists or we are careful.
    for k in keys[:-1]:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = curr[k]
    curr[keys[-1]] = parsed_value
    
    try:
        new_config = AuricConfig(**data)
        ConfigLoader.save(new_config)
        console.print(f"[green]Set '{key}' to:[/green]")
        console.print(parsed_value)
    except Exception as e:
        console.print(f"[red]Failed to set value (Validation Error): {e}[/red]")

@config_app.command("unset")
def config_unset(key: str):
    """Remove a configuration key."""
    config = load_config()
    data = config.model_dump(mode='json')
    
    keys = key.split('.')
    parent = None
    target_key = None
    
    try:
        curr = data
        for k in keys[:-1]:
            curr = curr[k]
        target_key = keys[-1]
        
        if target_key in curr:
            del curr[target_key]
            # Save
            new_config = AuricConfig(**data)
            ConfigLoader.save(new_config)
            console.print(f"[green]Unset '{key}'[/green]")
        else:
            console.print(f"[red]Key '{key}' not found.[/red]")
            
    except Exception:
        console.print(f"[red]Path '{key}' invalid.[/red]")

# --- Pairing Commands ---

@pairing_app.command("list")
def pairing_list(pact: str = typer.Argument(..., help="The pact name (e.g., discord)")):
    """List pending pairing requests."""
    from auric.core.pairing import PairingManager
    
    try:
        mgr = PairingManager()
        requests = mgr.list_requests(pact)
        
        if not requests:
            console.print(f"[yellow]No pending requests for {pact}.[/yellow]")
            return
            
        console.print(f"[bold green]Pending Requests for {pact}:[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Shortcode")
        table.add_column("User Name")
        table.add_column("User ID")
        table.add_column("Time")
        
        for code, data in requests.items():
            table.add_row(code, data["user_name"], data["user_id"], data["timestamp"])
            
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Error listing requests: {e}[/red]")

@pairing_app.command("approve")
def pairing_approve(
    pact: str = typer.Argument(..., help="The pact name (e.g., discord)"),
    shortcode: str = typer.Argument(..., help="The pairing shortcode")
):
    """Approve a pairing request."""
    from auric.core.pairing import PairingManager
    
    try:
        mgr = PairingManager()
        user_name = mgr.approve_request(pact, shortcode)
        
        if user_name:
            console.print(f"[bold green]Successfully approved {user_name} for {pact}.[/bold green]")
            console.print(f"They can now interact with the agent.")
        else:
            console.print(f"[red]Invalid code '{shortcode}' or request expired.[/red]")
            
    except Exception as e:
        console.print(f"[red]Error approving request: {e}[/red]")

if __name__ == "__main__":
    app()
