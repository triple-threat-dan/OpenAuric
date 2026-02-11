import os
import signal
import sys
import atexit
import psutil
import typer
import json5
import json
from pathlib import Path
from typing import Any, Optional
from rich.console import Console
from rich.syntax import Syntax
from rich.syntax import Syntax

app = typer.Typer(help="OpenAuric: The Recursive Agentic Warlock")
dashboard_app = typer.Typer(help="Manage the OpenAuric dashboard")
config_app = typer.Typer(help="Manage OpenAuric configuration")

app.add_typer(dashboard_app, name="dashboard")
app.add_typer(config_app, name="config")

console = Console()

CONFIG_PATH = Path("~/.auric/auric.json").expanduser()
PID_FILE = Path("~/.auric/auric.pid").expanduser()

# --- Config Helpers ---

def _get_config_path() -> Path:
    return CONFIG_PATH

def _load_config() -> dict:
    path = _get_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json5.load(f)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        return {}

def _save_config(data: dict):
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            # dumping with json5 or json. dump using json for now for compatibility/speed/standard format
            # but usually json5 libraries support dump. PyPi 'json5' supports dump.
            json5.dump(data, f, indent=4)
    except Exception as e:
        console.print(f"[red]Error saving config: {e}[/red]")

def _get_nested_value(data: dict, key: str) -> Any:
    keys = key.split('.')
    curr = data
    for k in keys:
        if isinstance(curr, dict) and k in curr:
            curr = curr[k]
        else:
            return None
    return curr

def _set_nested_value(data: dict, key: str, value: Any):
    keys = key.split('.')
    curr = data
    for k in keys[:-1]:
        if k not in curr or not isinstance(curr[k], dict):
            curr[k] = {}
        curr = curr[k]
    curr[keys[-1]] = value

def _unset_nested_value(data: dict, key: str) -> bool:
    keys = key.split('.')
    curr = data
    # Navigate to the parent of the target key
    for k in keys[:-1]:
        if isinstance(curr, dict) and k in curr:
            curr = curr[k]
        else:
            # Path doesn't exist
            return False
    
    last_key = keys[-1]
    if isinstance(curr, dict) and last_key in curr:
        del curr[last_key]
        return True
    return False

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
                console.print("Run 'auric stop' or delete ~/.auric/auric.pid if this is an error.")
                raise typer.Exit(1)
            else:
                console.print(f"[yellow]Found stale PID file ({old_pid}). Overwriting...[/yellow]")
        except ValueError:
             console.print("[yellow]Invalid PID file. Overwriting...[/yellow]")

    current_pid = os.getpid()
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
def restart():
    """Restart the Auric Daemon."""
    stop()
    start()

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
    config = _load_config()
    host = _get_nested_value(config, "gateway.host") or "localhost"
    port = _get_nested_value(config, "gateway.port") or 8067
    
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
    data = _load_config()
    val = _get_nested_value(data, key)
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
    data = _load_config()
    
    parsed_value = value
    if is_json:
        try:
            parsed_value = json5.loads(value)
        except Exception as e:
             console.print(f"[red]Invalid JSON5 value: {e}[/red]")
             raise typer.Exit(code=1)
    else:
        # Infer type
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
                    parsed_value = value # Treat as string
    
    _set_nested_value(data, key, parsed_value)
    _save_config(data)
    console.print(f"[green]Set '{key}' to:[/green]")
    console.print(parsed_value)

@config_app.command("unset")
def config_unset(key: str):
    """Remove a configuration key."""
    data = _load_config()
    if _unset_nested_value(data, key):
        _save_config(data)
        console.print(f"[green]Unset '{key}'[/green]")
    else:
        console.print(f"[red]Key '{key}' not found or path invalid.[/red]")

if __name__ == "__main__":
    app()
