$ErrorActionPreference = "Stop"

# --- Configuration ---
$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$AuricHome = "$RepoRoot\.auric"
$TemplateDir = "$RepoRoot\templates"
$StartupFolder = [System.Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "OpenAuric.lnk"

Write-Host ">>> Initiating OpenAuric First Contact Sequence..." -ForegroundColor Magenta

# --- 1. Pre-flight Checks ---

# Check Python version >= 3.11
if (Get-Command python -ErrorAction SilentlyContinue) {
    # Simple string concatenation
    $PythonVer = python -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))"
    $ReqVer = [Version]"3.11"
    $CurVer = [Version]$PythonVer
    
    if ($CurVer -lt $ReqVer) {
        Write-Error "[ERROR] Python 3.11+ is required. Found $PythonVer."
    }
    Write-Host "[OK] Python $PythonVer detected." -ForegroundColor Green
}
else {
    Write-Error "[ERROR] 'python' command not found."
}

# Check for uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[WARN] 'uv' not found. Installing via Astral script..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    
    # Refreshes Env vars for current session
    $UserPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $MachinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$UserPath;$MachinePath"
}
else {
    Write-Host "[OK] 'uv' is installed." -ForegroundColor Green
}

# --- 2. The Setup ---

Write-Host ">>> Setting up ~/.auric..." -ForegroundColor Cyan
if (-not (Test-Path $AuricHome)) {
    New-Item -ItemType Directory -Force -Path $AuricHome | Out-Null
}

# Copy templates without overwriting existing config
if (Test-Path $TemplateDir) {
    Write-Host ">>> Copying Default Knowledge Pack..." -ForegroundColor Cyan
    # Copy items, exclude existing to avoid overwrite
    Get-ChildItem -Path $TemplateDir -Recurse | ForEach-Object {
        $DestPath = $_.FullName.Replace($TemplateDir, $AuricHome)
        if (-not (Test-Path $DestPath)) {
            if ($_.PSIsContainer) {
                New-Item -ItemType Directory -Path $DestPath | Out-Null
            }
            else {
                Copy-Item -Path $_.FullName -Destination $DestPath
            }
        }
    }
    Write-Host "[OK] Templates copied (existing files preserved)." -ForegroundColor Green
}
else {
    Write-Warning "Template directory $TemplateDir not found."
}

# Permissions (ACLs)
Write-Host ">>> Securing Grimoire..." -ForegroundColor Cyan
$Acl = Get-Acl $AuricHome
$Acl.SetAccessRuleProtection($true, $false)
$Rule = New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
$Acl.AddAccessRule($Rule)
Set-Acl -Path $AuricHome -AclObject $Acl
Write-Host "[OK] ACLs applied: Only $env:USERNAME has access." -ForegroundColor Green

# --- 2.5 Package Installation ---

Write-Host ">>> Installing OpenAuric binary..." -ForegroundColor Cyan
if (Test-Path "$RepoRoot\pyproject.toml") {
    # Install from local source
    uv tool install "$RepoRoot" --force
    Write-Host "[OK] OpenAuric installed globally via uv." -ForegroundColor Green
}
else {
    Write-Warning "pyproject.toml not found. Skipping global tool install."
}

# --- 3. System Integration ---

Write-Host ">>> Configuring Auto-Start..." -ForegroundColor Cyan

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
# Try to find auric executable path
$AuricPath = (Get-Command auric -ErrorAction SilentlyContinue).Source
if (-not $AuricPath) {
    # Fallback to assuming standard uv location if not found in current path context
    $AuricPath = "$HOME\.local\bin\auric.exe"
}

$Shortcut.TargetPath = $AuricPath
$Shortcut.Arguments = "start"
$Shortcut.Description = "OpenAuric Daemon"
$Shortcut.Save()

Write-Host "[OK] Startup shortcut created at $ShortcutPath" -ForegroundColor Green

Write-Host ">>> OpenAuric installation complete." -ForegroundColor Magenta
Write-Host "    Run auric start to begin (or log out and back in)." -ForegroundColor Cyan
