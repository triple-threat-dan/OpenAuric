$ErrorActionPreference = "Stop"

# --- Configuration ---
$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$PidFile = "$RepoRoot\.auric\auric.pid"
$WasRunning = $false

Write-Host ">>> Initiating OpenAuric Update Sequence..." -ForegroundColor Magenta

# --- 1. Check if Auric is Running ---

if (Test-Path $PidFile) {
    $PidContent = Get-Content $PidFile -Raw
    if ($PidContent -match '^\d+$') {
        $AUricPid = [int]$PidContent
        $Process = Get-Process -Id $AUricPid -ErrorAction SilentlyContinue
        if ($Process) {
            Write-Host "[INFO] Auric is currently running (PID: $AUricPid)." -ForegroundColor Cyan
            $WasRunning = $true
            
            Write-Host ">>> Stopping Auric..." -ForegroundColor Cyan
            try {
                Stop-Process -Id $AUricPid -Force -ErrorAction Stop
                Write-Host "[OK] Auric stopped." -ForegroundColor Green
            }
            catch {
                Write-Warning "Failed to stop Auric gracefully: $_"
            }
            
            # Give it a moment to fully shutdown
            Start-Sleep -Seconds 2
        }
        else {
            Write-Host "[INFO] PID file exists but process is not running." -ForegroundColor Yellow
        }
    }
    # Clean up the PID file
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# --- 2. Git Pull ---

Write-Host ">>> Pulling latest code from git..." -ForegroundColor Cyan
Set-Location $RepoRoot

try {
    $GitOutput = git pull 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[ERROR] Git pull failed: $GitOutput"
        exit 1
    }
    
    if ($GitOutput -match "Already up to date") {
        Write-Host "[OK] Already up to date." -ForegroundColor Green
    }
    else {
        Write-Host "[OK] Repository updated:" -ForegroundColor Green
        Write-Host $GitOutput -ForegroundColor Gray
    }
}
catch {
    Write-Error "[ERROR] Git command failed. Is git installed and are you in a git repository?"
    exit 1
}

# --- 3. Reinstall Package ---

Write-Host ">>> Reinstalling OpenAuric package..." -ForegroundColor Cyan

if (Test-Path "$RepoRoot\pyproject.toml") {
    try {
        uv tool install "$RepoRoot" --force
        Write-Host "[OK] OpenAuric reinstalled successfully." -ForegroundColor Green
    }
    catch {
        Write-Error "[ERROR] Failed to reinstall package: $_"
        exit 1
    }
}
else {
    Write-Warning "pyproject.toml not found. Skipping package reinstall."
}

# --- 4. Restart if it was running ---

if ($WasRunning) {
    Write-Host ">>> Restarting Auric..." -ForegroundColor Cyan
    try {
        $AuricPath = (Get-Command auric -ErrorAction SilentlyContinue).Source
        if (-not $AuricPath) {
            $AuricPath = "$env:USERPROFILE\.local\bin\auric.exe"
        }
        
        if (Test-Path $AuricPath) {
            # Start auric in a new process so it doesn't block this script
            Start-Process -FilePath $AuricPath -ArgumentList "start" -WindowStyle Hidden
            Write-Host "[OK] Auric restarted." -ForegroundColor Green
        }
        else {
            Write-Warning "Could not find auric executable. Please start manually with: auric start"
        }
    }
    catch {
        Write-Warning "Failed to restart Auric: $_. Please start manually with: auric start"
    }
}

Write-Host ">>> OpenAuric update complete." -ForegroundColor Magenta

if (-not $WasRunning) {
    Write-Host "    Run 'auric start' to begin." -ForegroundColor Cyan
}