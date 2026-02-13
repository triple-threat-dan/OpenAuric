 #!/bin/bash
set -e

# --- Configuration ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AURIC_HOME="$REPO_ROOT/.auric"
TEMPLATE_DIR="$REPO_ROOT/templates"
SERVICE_FILE="$HOME/.config/systemd/user/auric.service"

echo "🔮 Initiating OpenAuric First Contact Sequence..."

# --- 1. Pre-flight Checks ---

# Check Python version >= 3.11
if ! command -v python3 &> /dev/null; then
  echo "❌ Error: python3 is not installed."
  exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
REQUIRED_VERSION="3.11"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "❌ Error: Python 3.11+ is required. Found $PYTHON_VERSION."
    exit 1
fi
echo "✅ Python $PYTHON_VERSION detected."

# Check for uv
if ! command -v uv &> /dev/null; then
    echo "⚠️  'uv' not found. Installing via Astral's script..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Ensure uv is in path for this session if just installed
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "✅ 'uv' is installed."
fi

# --- 2. The Setup ---

echo "📂 Setting up ~/.auric..."
mkdir -p "$AURIC_HOME"

# Copy templates without overwriting existing config
if [ -d "$TEMPLATE_DIR" ]; then
    echo "📜 Copying Default Knowledge Pack..."
    # rsync is safer but cp -n is standard. We'll use a loop to be safe and interactive-ish logic implies soft skip.
    # We want to copy contents of .auric/ to ~/.auric/
    # Using cp -rn to not overwrite
    cp -rn "$TEMPLATE_DIR/"* "$AURIC_HOME/" || true
    echo "✅ Templates copied (existing files preserved)."
else
    echo "⚠️  Warning: Template directory $TEMPLATE_DIR not found within repo."
fi

# Permissions
echo "🔒 Securing Grimoire..."
if [ -f "$AURIC_HOME/auric.json" ]; then
    chmod 600 "$AURIC_HOME/auric.json"
fi
if [ -f "$AURIC_HOME/SOUL.md" ]; then
    chmod 600 "$AURIC_HOME/SOUL.md"
fi
echo "✅ Permissions applied."

# --- 2.5 Package Installation ---

echo "📦 Installing OpenAuric binary..."
if [ -f "$REPO_ROOT/pyproject.toml" ]; then
    uv tool install "$REPO_ROOT" --force
    echo "✅ OpenAuric installed globally via uv."
    
    # Ensure local bin is in PATH for this session so we can find 'auric' immediately
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "⚠️  pyproject.toml not found. Skipping global tool install."
fi

# --- 3. System Integration ---

# Check if running in a systemd environment (WSL2 might not have it active)
if pidof systemd >/dev/null 2>&1 || pidof systemd-init >/dev/null 2>&1; then
    echo "⚙️  Configuring Systemd Service..."
    mkdir -p "$(dirname "$SERVICE_FILE")"

    # Assume 'uv tool install' has been or will be run, but for development/source installation,
    # we point to the venv python in the repository or rely on 'auric' being in PATH.
    # Since install.sh is typically run from the repo:
    # Option A: Run from source venv (common for devs)
    # Option B: Run installed tool.
    # The requirement says "Should point to the `auric start` entry point (assumes `uv tool install .` or similar setup has occurred...)"
    
    # We will locate `auric` path.
    AURIC_BIN=$(which auric || echo "$HOME/.local/bin/auric")
    
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=OpenAuric "The Recursive Agentic Warlock" Daemon
After=network.target

[Service]
ExecStart=$AURIC_BIN start
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable auric.service
    systemctl --user start auric.service
    echo "✅ systemd service started and enabled."
else
    echo "⚠️  Systemd not detected (common in some WSL configurations)."
    echo "   You may need to run 'auric start' manually or configure your specific init system."
fi

echo "✨ OpenAuric installation complete. The Warlock awaits."
