$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$AuricHome = "$RepoRoot\.auric"
$StartupFolder = [System.Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "OpenAuric.lnk"

Write-Host ">>> Initiating OpenAuric Removal Protocol..." -ForegroundColor Red

# --- 1. The Clean Slate ---

# Remove Shortcut
if (Test-Path $ShortcutPath) {
    Remove-Item -Path $ShortcutPath -Force
    Write-Host "[OK] Startup shortcut removed." -ForegroundColor Green
}

# Prompt for Data Removal
Write-Host "This will permanently delete all memory and configuration in $AuricHome." -ForegroundColor Yellow
$Confirmation = Read-Host "Are you sure you want to proceed? (y/N)"
if ($Confirmation -notmatch "^[Yy]$") {
    Write-Host "[INFO] Operation cancelled. Configuration preserved." -ForegroundColor Gray
    exit 0
}

# Stop any running auric processes to allow deletion
$Processes = Get-Process -Name "auric" -ErrorAction SilentlyContinue
if ($Processes) {
    Write-Host ">>> Stopping running auric processes..." -ForegroundColor Cyan
    Stop-Process -Name "auric" -Force
}

if (Test-Path $AuricHome) {
    Remove-Item -Path $AuricHome -Recurse -Force
    Write-Host "[OK] $AuricHome directory obliterated." -ForegroundColor Green
}

# Uninstall Package
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host ">>> Uninstalling open-auric via uv..." -ForegroundColor Cyan
    uv tool uninstall open-auric
}
else {
    Write-Host "[INFO] 'uv' not found, skipping tool uninstall." -ForegroundColor Gray
}

Write-Host ">>> OpenAuric uninstalled throughout the system." -ForegroundColor Magenta
