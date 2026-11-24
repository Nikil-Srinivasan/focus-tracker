# One-time setup. Run from the repo root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { py -3 -m venv .venv }
    } else {
        python -m venv .venv
    }
    if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Could not create .venv. Install Python 3.11+ from python.org." }
}
& .venv\Scripts\python.exe -m pip install --upgrade pip
& .venv\Scripts\python.exe -m pip install -e ".[dev]"

# Block screenshots/databases from ever being committed to the public repo.
git config core.hooksPath scripts/hooks

if (-not (Test-Path "config.toml")) {
    Copy-Item config.example.toml config.toml
    Write-Host "Created config.toml (git-ignored). Edit it to change settings."
}
Write-Host ""
Write-Host "Done. Try it now:   .venv\Scripts\python.exe -m focus_tracker --verbose"
Write-Host "Start at login:     powershell -ExecutionPolicy Bypass -File scripts\install_startup.ps1"
