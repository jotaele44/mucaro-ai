$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RootDir

Write-Host "BOOTSTRAP_START root=$RootDir"

$pythonCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
    $pythonArgs = @("-3.11")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
    $pythonArgs = @()
} else {
    Write-Error "ERROR_PYTHON_MISSING install Python 3.11+ and rerun"
    exit 2
}

& $pythonCmd @pythonArgs scripts/preflight.py --create-env
& $pythonCmd @pythonArgs -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location frontend
    npm ci
    Pop-Location
    Write-Host "FRONTEND_DEPS_OK"
} else {
    Write-Host "WARN_NPM_MISSING frontend deps skipped; install Node 20+ later"
}

& .\.venv\Scripts\python.exe scripts/preflight.py --create-env --strict-imports
& .\.venv\Scripts\python.exe scripts/release_check.py

Write-Host "BOOTSTRAP_OK"
Write-Host "NEXT_BACKEND: .\.venv\Scripts\Activate.ps1 ; uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000"
Write-Host "NEXT_FRONTEND: cd frontend ; npm run dev"
