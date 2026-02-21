#!/bin/bash
set -e

# --- Configuration ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$REPO_ROOT/.auric/auric.pid"
WAS_RUNNING=false

echo "🔮 Initiating OpenAuric Update Sequence..."

# --- 1. Check if Auric is Running ---

if [ -f "$PID_FILE" ]; then
    AURIC_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$AURIC_PID" ] && [ "$AURIC_PID" -eq "$AURIC_PID" ] 2>/dev/null; then
        if kill -0 "$AURIC_PID" 2>/dev/null; then
            echo "ℹ️  Auric is currently running (PID: $AURIC_PID)."
            WAS_RUNNING=true
            
            echo "🛑 Stopping Auric..."
            if kill "$AURIC_PID" 2>/dev/null; then
                echo "✅ Auric stopped."
            else
                echo "⚠️  Failed to stop Auric gracefully."
            fi
            
            # Give it a moment to fully shutdown
            sleep 2
        else
            echo "ℹ️  PID file exists but process is not running."
        fi
    fi
    # Clean up the PID file
    rm -f "$PID_FILE"
fi

# --- 2. Git Pull ---

echo "⬇️  Pulling latest code from git..."
cd "$REPO_ROOT"

if ! command -v git &> /dev/null; then
    echo "❌ Error: git is not installed."
    exit 1
fi

GIT_OUTPUT=$(git pull 2>&1) || {
    echo "❌ Error: Git pull failed: $GIT_OUTPUT"
    exit 1
}

if echo "$GIT_OUTPUT" | grep -q "Already up to date"; then
    echo "✅ Already up to date."
else
    echo "✅ Repository updated:"
    echo "$GIT_OUTPUT"
fi

# --- 3. Reinstall Package ---

echo "📦 Reinstalling OpenAuric package..."

if [ -f "$REPO_ROOT/pyproject.toml" ]; then
    uv tool install "$REPO_ROOT" --force
    echo "✅ OpenAuric reinstalled successfully."
else
    echo "⚠️  Warning: pyproject.toml not found. Skipping package reinstall."
fi

# --- 4. Restart if it was running ---

if [ "$WAS_RUNNING" = true ]; then
    echo "🚀 Restarting Auric..."
    
    AURIC_BIN=$(which auric 2>/dev/null || echo "$HOME/.local/bin/auric")
    
    if [ -x "$AURIC_BIN" ]; then
        # Start auric in background so it doesn't block this script
        nohup "$AURIC_BIN" start > /dev/null 2>&1 &
        echo "✅ Auric restarted."
    else
        echo "⚠️  Warning: Could not find auric executable. Please start manually with: auric start"
    fi
fi

echo "✨ OpenAuric update complete."

if [ "$WAS_RUNNING" = false ]; then
    echo "   Run 'auric start' to begin."
fi