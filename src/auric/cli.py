import typer
import json
import urllib.request
import urllib.error
from pathlib import Path
from rich.console import Console
from auric.core.config import AURIC_ROOT, load_config

# --- Apps ---
app = typer.Typer(help="OpenAuric: The Recursive Agentic Warlock")
dashboard_app = typer.Typer(help="Manage the OpenAuric dashboard")
config_app = typer.Typer(help="Manage OpenAuric configuration")
spells_app = typer.Typer(help="Manage Spells (Skills)")
pairing_app = typer.Typer(help="Manage Device/User Pairing")
memory_app = typer.Typer(help="Manage Agent Memory")
focus_app = typer.Typer(help="Manage Agent Focus")
sessions_app = typer.Typer(help="Manage Active Sessions")
token_app = typer.Typer(help="Manage Web UI Security Token")

app.add_typer(dashboard_app, name="dashboard")
app.add_typer(config_app, name="config")
app.add_typer(spells_app, name="spells")
app.add_typer(pairing_app, name="pairing")
app.add_typer(memory_app, name="memory")
app.add_typer(focus_app, name="focus")
app.add_typer(sessions_app, name="sessions")
app.add_typer(token_app, name="token")

console = Console()
PID_FILE = AURIC_ROOT / "auric.pid"

SKILL_TEMPLATE = """---
name: {name}
description: TODO: Complete and informative explanation of what the skill does and when to use it.
---

# {name}

## Overview
[TODO: 1-2 sentences explaining what this skill enables]

## Resources
- scripts/: Executable code (.py, .sh, .ps1)
- references/: Documentation/Reference material (loaded into context)
- assets/: Static files (not loaded into context)
"""

def _api_request(endpoint: str, method: str = "GET", data: dict = None, timeout: int = 5):
    """Internal helper for API calls to the daemon."""
    config = load_config()
    url = f"http://127.0.0.1:{config.gateway.port}{endpoint}"
    token = config.gateway.web_ui_token
    
    encoded_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=encoded_data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
        
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except Exception:
        return None, None

def send_message(text: str, wait: bool = True):
    """Internal helper to send a message to the daemon API and optionally wait for response."""
    import time
    
    status, status_data = _api_request("/api/status")
    session_id = status_data.get("current_session_id") if status_data else None
    last_msg_count = len(status_data.get("chat_history", [])) if status_data else 0

    payload = {"message": text, "source": "CLI", "session_id": session_id}
    status, _ = _api_request("/api/chat", method="POST", data=payload, timeout=10)
    
    if status != 200:
        console.print("[yellow]Daemon unreachable. Use 'auric start' to launch it.[/yellow]")
        return

    if not wait or not session_id:
        console.print(f"[green]Sent: {text}[/green]")
        return

    console.print(f"[dim]Sent: {text}[/dim]")
    console.print("[dim italic]Agent is thinking...[/dim italic]")
    
    start_time = time.time()
    while time.time() - start_time < 120:
        status, data = _api_request("/api/status")
        if status == 200 and data:
            history = data.get("chat_history", [])
            if len(history) > last_msg_count:
                for msg in history[last_msg_count:]:
                    role, content = msg.get("level"), msg.get("message", "")
                    if role in ("THOUGHT", "TOOL"):
                        console.print(f"[dim]⚡ {content}[/dim]")
                    elif role == "AGENT":
                        console.print(f"\n[bold green]ALISS:[/bold green] {content}")
                        return
                    elif role == "ERROR":
                        console.print(f"[bold red]Error:[/bold red] {content}")
                        return
                last_msg_count = len(history)
        time.sleep(0.5)

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    message: str = typer.Option(None, "--message", "-m", help="Send a message directly to the agent"),
    wait: bool = typer.Option(True, "--wait/--no-wait", "-w/-W", help="Wait for the agent's response")
):
    """OpenAuric: The Recursive Agentic Warlock."""
    if message:
        send_message(message, wait=wait)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()

@app.command()
def message(
    content: str = typer.Argument(..., help="The message content"),
    wait: bool = typer.Option(True, "--wait/--no-wait", "-w/-W", help="Wait for response")
):
    """Send a message to the agent."""
    send_message(content, wait=wait)

# --- Daemon ---

@app.command()
def start():
    """Start the Auric Daemon."""
    import os, psutil, atexit, asyncio
    from auric.core.daemon import run_daemon
    from fastapi import FastAPI
    
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if psutil.pid_exists(pid):
                console.print(f"[bold red]Daemon running (PID {pid}).[/bold red]")
                raise typer.Exit(1)
        except ValueError: pass

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink() if PID_FILE.exists() else None)
    
    console.print(f"[green]Starting Daemon (PID {os.getpid()})...[/green]")
    try:
        asyncio.run(run_daemon(tui_app=None, api_app=FastAPI()))
    except KeyboardInterrupt: pass
    finally:
        if PID_FILE.exists(): PID_FILE.unlink()

@app.command()
def stop(force: bool = typer.Option(False, "--force", "-f")):
    """Stop the Auric Daemon."""
    import psutil, time
    
    # 1. Try API Shutdown first
    status, _ = _api_request("/api/shutdown", method="POST")
    if status == 200:
        console.print("[green]Graceful shutdown initiated via API...[/green]")
        # Wait for PID file to disappear
        for _ in range(10):
            if not PID_FILE.exists():
                console.print("[green]Daemon stopped gracefully.[/green]")
                return
            time.sleep(0.5)

    # 2. Fallback to process termination if API failed or PID still exists
    if not PID_FILE.exists(): return
    try:
        pid = int(PID_FILE.read_text().strip())
        proc = psutil.Process(pid)
        console.print(f"[yellow]Triggering termination for PID {pid}...[/yellow]")
        proc.terminate()
        proc.wait(timeout=5)
        console.print("[green]Daemon stopped (terminated).[/green]")
    except (psutil.NoSuchProcess, psutil.TimeoutExpired, ValueError):
        if force:
            proc.kill()
            console.print("[red]Daemon killed.[/red]")
    finally:
        if PID_FILE.exists(): PID_FILE.unlink()

@app.command()
def heartbeat():
    """Manual system heartbeat."""
    import asyncio
    from auric.core.database import AuditLogger
    
    status, _ = _api_request("/api/heartbeat", method="POST")
    if status == 200:
        console.print("[bold green]Heartbeat triggered via Daemon.[/bold green]")
        return

    async def log_offline():
        logger = AuditLogger()
        await logger.init_db()
        await logger.log_heartbeat(status="MANUAL", meta={"source": "cli"})
        console.print("[bold green]Heartbeat logged (Offline).[/bold green]")

    console.print("[yellow]Daemon unreachable. Logging offline...[/yellow]")
    asyncio.run(log_offline())

@app.command()
def restart():
    """Restart the Auric Daemon."""
    stop()
    start()

# --- Token ---

@token_app.callback(invoke_without_command=True)
def token_main(ctx: typer.Context):
    """Get or generate the Web UI token."""
    if ctx.invoked_subcommand is None:
        from auric.core.config import load_config
        token = load_config().gateway.web_ui_token
        if token:
            console.print(f"[green]Token: [bold cyan]{token}[/bold cyan][/green]")
        else:
            token_new()

@token_app.command("new")
def token_new():
    """Generate a NEW Web UI token."""
    import secrets
    from auric.core.config import load_config, ConfigLoader
    config = load_config()
    config.gateway.web_ui_token = secrets.token_urlsafe(32)
    ConfigLoader.save(config)
    console.print(f"[green]New Token: [bold cyan]{config.gateway.web_ui_token}[/bold cyan][/green]")

# --- Spells ---

@spells_app.command("list")
def spells_list():
    """List available spells."""
    from rich.table import Table
    from auric.spells.tool_registry import ToolRegistry
    registry = ToolRegistry(load_config())
    if not registry._spells:
        console.print("[yellow]No spells found.[/yellow]")
        return
    table = Table(header_style="bold magenta")
    table.add_column("Name"); table.add_column("Type"); table.add_column("Description")
    for n, d in registry._spells.items():
        table.add_row(n, "Exec" if d["script"] else "Inst", d["description"])
    console.print(table)

@spells_app.command("create")
def spells_create(name: str):
    """Create a new spell."""
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise typer.BadParameter("Invalid name.")
    path = Path("./.auric/grimoire").expanduser() / name
    if path.exists(): raise typer.BadParameter("Exists.")
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(SKILL_TEMPLATE.format(name=name))
    console.print(f"[green]Created {path}[/green]")

@spells_app.command("reload")
def spells_reload():
    """Reload spells."""
    from auric.spells.tool_registry import ToolRegistry
    try:
        ToolRegistry(load_config())
        console.print("[green]Local index updated.[/green]")
    except Exception: pass
    status, _ = _api_request("/spells/reload", method="POST")
    if status == 200: console.print("[green]Daemon reloaded.[/green]")
    else: console.print("[yellow]Daemon unreachable.[/yellow]")

# --- Dashboard ---

@dashboard_app.callback(invoke_without_command=True)
def dashboard_main(ctx: typer.Context):
    if ctx.invoked_subcommand is None: dashboard_start()

@dashboard_app.command("start")
def dashboard_start():
    import webbrowser
    config = load_config()
    url = f"http://{config.gateway.host}:{config.gateway.port}"
    console.print(f"[green]Opening {url}...[/green]")
    webbrowser.open(url)

# --- Config ---

@config_app.command("get")
def config_get(key: str):
    config = load_config().model_dump(mode='json')
    curr = config
    for k in key.split('.'):
        curr = curr.get(k) if isinstance(curr, dict) else None
    if curr is not None:
        console.print_json(data=curr) if isinstance(curr, (dict, list)) else console.print(str(curr))
    else: console.print(f"[red]'{key}' not found.[/red]")

@config_app.command("set")
def config_set(key: str, value: str, is_json: bool = False):
    import json5
    from auric.core.config import AuricConfig, ConfigLoader
    config = load_config().model_dump(mode='json')
    val = json5.loads(value) if is_json else value
    if not is_json and isinstance(val, str):
        if val.lower() == "true": val = True
        elif val.lower() == "false": val = False
        else:
            try: val = int(val)
            except:
                try: val = float(val)
                except: pass
    curr = config
    keys = key.split('.')
    for k in keys[:-1]:
        curr = curr.setdefault(k, {})
    curr[keys[-1]] = val
    ConfigLoader.save(AuricConfig(**config))
    console.print(f"[green]Set '{key}'[/green]")

@config_app.command("unset")
def config_unset(key: str):
    from auric.core.config import AuricConfig, ConfigLoader
    config = load_config().model_dump(mode='json')
    curr = config
    keys = key.split('.')
    try:
        for k in keys[:-1]: curr = curr[k]
        del curr[keys[-1]]
        ConfigLoader.save(AuricConfig(**config))
        console.print(f"[green]Unset '{key}'[/green]")
    except: console.print(f"[red]'{key}' invalid.[/red]")

# --- Other ---

@pairing_app.command("list")
def pairing_list(pact: str):
    from rich.table import Table
    from auric.core.pairing import PairingManager
    reqs = PairingManager().list_requests(pact)
    if not reqs: return console.print("No requests.")
    table = Table(); table.add_column("Code"); table.add_column("User"); table.add_column("ID")
    for c, d in reqs.items(): table.add_row(c, d["user_name"], d["user_id"])
    console.print(table)

@pairing_app.command("approve")
def pairing_approve(pact: str, shortcode: str):
    from auric.core.pairing import PairingManager
    name = PairingManager().approve_request(pact, shortcode)
    console.print(f"Approved {name}") if name else console.print("Invalid code.")

@memory_app.command("reindex")
def memory_reindex():
    import asyncio
    from auric.memory.librarian import GrimoireLibrarian
    async def run(): await GrimoireLibrarian().start_reindexing()
    asyncio.run(run())

@focus_app.command("reset")
def focus_reset(force: bool = False):
    from auric.memory.focus_manager import FocusManager
    if not force and not typer.confirm("Reset focus?"): raise typer.Abort()
    FocusManager(AURIC_ROOT / "memories" / "FOCUS.md").clear()
    console.print("Focus reset.")

@focus_app.command("get")
def focus_get(raw: bool = False):
    f = AURIC_ROOT / "memories" / "FOCUS.md"
    if not f.exists(): return
    if raw: console.print(f.read_text())
    else:
        from rich.markdown import Markdown
        console.print(Markdown(f.read_text()))

@sessions_app.command("list")
def sessions_list():
    from rich.table import Table
    from auric.core.session_router import SessionRouter
    r = SessionRouter()
    active = r.list_active_contexts()
    if not active: return console.print("No active sessions.")
    table = Table(); table.add_column("Context"); table.add_column("ID")
    for c, s in active.items(): table.add_row(c, s)
    console.print(table)

@sessions_app.command("closeall")
def sessions_closeall():
    from auric.core.session_router import SessionRouter
    if not typer.confirm("Close all?"): return

    # 1. Try API first (Graceful + Summarized)
    status, data = _api_request("/api/sessions/closeall", method="POST")
    if status == 200:
        console.print(f"[green]Closed {data.get('closed_count', 0)} sessions and summarized {data.get('summarized', 0)} sessions via Daemon.[/green]")
        return

    # 2. Offline Fallback (No Summarization)
    console.print("[yellow]Daemon unreachable. Closing sessions offline (no summarization)...[/yellow]")
    closed = SessionRouter().close_all_sessions()
    console.print(f"[green]Closed {len(closed)} sessions.[/green]")

if __name__ == "__main__": app()
