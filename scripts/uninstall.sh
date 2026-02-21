#!/bin/bash
set -e

SERVICE_FILE="$HOME/.config/systemd/user/auric.service"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AURIC_HOME="$REPO_ROOT/.auric"

echo "🛑 Initiating OpenAuric Removal Protocol..."

# --- 1. The Clean Slate ---

# Stop Service
if systemctl --user is-active --quiet auric.service 2>/dev/null; then
    echo "⚙️  Stopping auric.service..."
    systemctl --user stop auric.service
    systemctl --user disable auric.service
fi

if [ -f "$SERVICE_FILE" ]; then
    rm "$SERVICE_FILE"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "✅ Service file removed."
fi

# Prompt for Data Removal
echo "This will permanently delete all memory and configuration in $AURIC_HOME."
read -p "Are you sure you want to proceed? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "🚫 Operation cancelled. Configuration preserved."
    exit 0
fi

if [ -d "$AURIC_HOME" ]; then
    rm -rf "$AURIC_HOME"
    echo "✅ ./.auric directory obliterated."
fi

# Uninstall Package
if command -v uv &> /dev/null; then
    echo "📦 Uninstalling open-auric via uv..."
    uv tool uninstall open-auric || echo "⚠️  Could not uninstall via uv tool (maybe it wasn't installed that way)."
else
    echo "ℹ️  'uv' not found, skipping tool uninstall."
fi

echo "👋 OpenAuric uninstalled throughout the system."
