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

# Supported: 3.11-3.13. Pinned rasterio 1.4.3 publishes no wheels for 3.14+, so pip would try a GDAL source build.
$versionCheck = "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)"

# Probing a runtime that is not installed writes to stderr; with ErrorActionPreference=Stop,
# Windows PowerShell 5.1 turns redirected native stderr into a terminating error, so relax it here.
function Test-PythonCandidate {
    param([string]$Command, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command @Arguments -c $versionCheck 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}

$pythonCmd = $null
$pythonArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($selector in @("-3.13", "-3.12", "-3.11")) {
        if (Test-PythonCandidate "py" @($selector)) {
            $pythonCmd = "py"
            $pythonArgs = @($selector)
            break
        }
    }
}
if (-not $pythonCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Test-PythonCandidate "python" @()) {
        $pythonCmd = "python"
    }
}
if (-not $pythonCmd) {
    Write-Host "ERROR_PYTHON_UNSUPPORTED_OR_MISSING install Python 3.11-3.13 from python.org (enable 'Add to PATH') and rerun, or double-click ""Mucaro AI.cmd"", which brings its own Python 3.12"
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
