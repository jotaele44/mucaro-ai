$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RootDir

Write-Host "BOOTSTRAP_START root=$RootDir"

# Windows PowerShell 5.1 does not stop on native-command failures, so check every exit code.
function Invoke-Checked {
    param([string]$Label, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "BOOTSTRAP_FAILED step=$Label returncode=$LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

$versionCheck = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
$pythonCmd = $null
$pythonArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -c $versionCheck
    if ($LASTEXITCODE -eq 0) {
        $pythonCmd = "py"
        $pythonArgs = @("-3")
    }
}
if (-not $pythonCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python -c $versionCheck
    if ($LASTEXITCODE -eq 0) {
        $pythonCmd = "python"
    }
}
if (-not $pythonCmd) {
    Write-Host "ERROR_PYTHON_TOO_OLD_OR_MISSING install Python 3.11+ from python.org (enable 'Add to PATH') and rerun"
    exit 2
}
$pythonVersion = & $pythonCmd @pythonArgs -c "import platform; print(platform.python_version())"
Write-Host "PYTHON_SELECTED $pythonCmd $pythonVersion"

& $pythonCmd @pythonArgs scripts/preflight.py --create-env
Invoke-Checked "create_venv" { & $pythonCmd @pythonArgs -m venv .venv }
Invoke-Checked "pip_upgrade" { & .\.venv\Scripts\python.exe -m pip install --upgrade pip }
Invoke-Checked "pip_install" { & .\.venv\Scripts\python.exe -m pip install -e ".[dev]" }

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location frontend
    try {
        Invoke-Checked "npm_ci" { npm ci --registry=https://registry.npmjs.org/ }
    } finally {
        Pop-Location
    }
    Write-Host "FRONTEND_DEPS_OK"
} else {
    Write-Host "WARN_NPM_MISSING frontend deps skipped; install Node 20+ from nodejs.org and rerun"
}

Invoke-Checked "preflight" { & .\.venv\Scripts\python.exe scripts/preflight.py --create-env --strict-imports }
Invoke-Checked "release_check" { & .\.venv\Scripts\python.exe scripts/release_check.py }

Write-Host "BOOTSTRAP_OK"
Write-Host "NEXT_START: .\.venv\Scripts\Activate.ps1 ; python scripts\launch_app.py"
Write-Host "NEXT_OPEN: http://127.0.0.1:8000"
Write-Host "DEV_BACKEND: .\.venv\Scripts\Activate.ps1 ; python scripts\dev_start.py --backend-only"
Write-Host "DEV_FRONTEND: .\.venv\Scripts\Activate.ps1 ; python scripts\dev_start.py --frontend-only"
