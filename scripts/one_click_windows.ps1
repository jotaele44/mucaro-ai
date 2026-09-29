# Stage 0 of the double-click setup on Windows. Started by "Start Mucaro AI.exe" with --ui (no console window;
# progress is shown on the browser page .mucaro-runtime\ui\progress.html) or by the console fallback
# "Mucaro AI.cmd".
#
# Provisions pinned, checksum-verified private runtimes inside .mucaro-runtime\
# (uv, Node, CPython) and the project .venv, then hands off to scripts\one_click.py,
# which installs dependencies, builds the UI, and launches the app.
#
# Keep this file ASCII only: Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.
# Native commands are never run with 2>&1: in PowerShell 5.1 that turns their first stderr line
# into a terminating error. Every native call is checked through $LASTEXITCODE instead.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $Root
$Runtime = Join-Path $Root ".mucaro-runtime"
$LogDir = Join-Path $Runtime "logs"
$LockDir = Join-Path $Runtime "setup.lock"
$PinsFile = Join-Path $Root "scripts\one_click_pins.env"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$MinFreeBytes = 2GB
$script:Log = $null
$script:LockToken = $null
$ScriptArgs = @($args)
$UiMode = $ScriptArgs -contains "--ui"
$NoBrowser = $ScriptArgs -contains "--no-browser"
$Page = Join-Path $Runtime "ui\progress.html"
$PageTemplate = Join-Path $Root "scripts\one_click_progress.html"

function ConvertTo-PageText([string]$Text) {
    # Same escaping as Python's html.escape(quote=True), so all three renderers produce identical pages.
    return $Text.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;").Replace('"', "&quot;").Replace("'", "&#x27;")
}

function Write-ProgressPage([string]$State, [int]$Step, [string]$Title, [string]$Detail) {
    # PowerShell copy of scripts\progress_page.py. Best effort: a page that cannot be written never stops setup.
    if (-not $UiMode) { return }
    try {
        $total = 6
        if ($State -eq "ready" -or $State -eq "stopped") { $pct = 100 } else { $pct = [int][Math]::Floor($Step * 100 / $total) }
        $refresh = ""
        if ($State -eq "running") { $refresh = '<meta http-equiv="refresh" content="1">' }
        $logText = ""
        if ($script:Log) { $logText = $script:Log }
        $html = [IO.File]::ReadAllText($PageTemplate)
        $html = $html.Replace("{{REFRESH}}", $refresh)
        $html = $html.Replace("{{STATE}}", $State).Replace("{{STEP}}", "$Step").Replace("{{TOTAL}}", "$total").Replace("{{PERCENT}}", "$pct")
        $html = $html.Replace("{{TITLE}}", (ConvertTo-PageText $Title)).Replace("{{DETAIL}}", (ConvertTo-PageText $Detail))
        $html = $html.Replace("{{LOG}}", (ConvertTo-PageText $logText)).Replace("{{URL}}", "")
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Page) | Out-Null
        [IO.File]::WriteAllText("$Page.tmp", $html)
        Move-Item -LiteralPath "$Page.tmp" -Destination $Page -Force
    } catch { $null = $_ }
}

function Write-SetupLog([string]$Text) {
    Write-Host $Text
    if ($script:Log) {
        # Logging is best effort; the console copy of the message is the one that matters.
        try { [IO.File]::AppendAllText($script:Log, $Text + "`r`n") } catch { $null = $_ }
    }
}

function Exit-Lock {
    if (-not $script:LockToken) { return }
    $holder = ""
    try { $holder = ([IO.File]::ReadAllText((Join-Path $LockDir "pid"))).Trim() } catch { return }
    if ($holder -eq $script:LockToken) {
        Remove-Item -LiteralPath $LockDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Stop-Setup([string]$Code, [string]$Detail) {
    Write-ProgressPage "error" 0 "Mucaro AI setup did not finish" "${Code}: $Detail"
    Write-SetupLog ""
    Write-SetupLog "$Code $Detail"
    Write-SetupLog ""
    Write-SetupLog "Mucaro AI setup did not finish."
    if ($script:Log) { Write-SetupLog "Details: $($script:Log)" }
    Write-SetupLog "Nothing outside this folder was changed. Fix the problem named above (usually the internet connection), then double-click again; setup resumes where it stopped."
    Exit-Lock
    exit 2
}

try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
} catch {
    Write-Host "ERROR_FOLDER_NOT_WRITABLE cannot write inside $Root; move the mucaro-ai folder somewhere writable (for example your Documents or a Developer folder) and double-click again"
    exit 2
}
$script:Log = Join-Path $LogDir ("setup-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
[IO.File]::WriteAllText($script:Log, "")

if (-not (Test-Path -LiteralPath $PinsFile)) { Stop-Setup "ERROR_PINS_MISSING" $PinsFile }
$Pins = @{}
foreach ($line in [IO.File]::ReadAllLines($PinsFile)) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    $index = $trimmed.IndexOf("=")
    if ($index -gt 0) { $Pins[$trimmed.Substring(0, $index).Trim()] = $trimmed.Substring($index + 1).Trim() }
}
foreach ($key in @("UV_VERSION", "UV_SHA256_x86_64_pc_windows_msvc", "NODE_VERSION", "NODE_SHA256_win_x64", "PYTHON_VERSION")) {
    if (-not $Pins.ContainsKey($key) -or -not $Pins[$key]) { Stop-Setup "ERROR_PINS_INCOMPLETE" "$key missing from scripts\one_click_pins.env" }
}

# Keep the user's shell customizations from leaking into the private runtimes.
foreach ($name in @("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "NODE_OPTIONS")) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
$env:UV_NO_CONFIG = "1"
$env:UV_PYTHON_PREFERENCE = "only-managed"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime "python"
$env:UV_PYTHON_BIN_DIR = Join-Path $Runtime "python-bin"
$env:UV_CACHE_DIR = Join-Path $Runtime "cache\uv"
$env:UV_TOOL_DIR = Join-Path $Runtime "uv-tools"

$UvHome = Join-Path $Runtime ("uv\" + $Pins["UV_VERSION"])
$UvBin = Join-Path $UvHome "uv.exe"
$NodeHome = Join-Path $Runtime ("node\" + $Pins["NODE_VERSION"])
$NodeExe = Join-Path $NodeHome "node.exe"

function Test-LockHolderAlive([string]$Holder) {
    if (-not $Holder) { return $false }
    try { $process = Get-Process -Id ([int]$Holder) -ErrorAction Stop } catch { return $false }
    # Guard against a recycled pid: the holder must still be this setup (PowerShell stage or one_click.py).
    return @("powershell", "pwsh", "python") -contains $process.ProcessName.ToLowerInvariant()
}

function Enter-Lock {
    $waited = 0
    $emptyChecks = 0
    while ($true) {
        $created = $false
        try {
            New-Item -ItemType Directory -Path $LockDir -ErrorAction Stop | Out-Null
            $created = $true
        } catch { $created = $false }  # already exists: another window holds or held the lock
        if ($created) { break }
        $holder = ""
        try { $holder = ([IO.File]::ReadAllText((Join-Path $LockDir "pid"))).Trim() } catch { $holder = "" }  # pid not written yet
        if (-not $holder -and $emptyChecks -lt 5) {
            # The other window may have created the lock a moment ago; give it time to write its pid.
            $emptyChecks++
            Start-Sleep -Seconds 1
            continue
        }
        if (Test-LockHolderAlive $holder) {
            if ($waited -eq 0) { Write-SetupLog "SETUP_WAITING another Mucaro AI window (pid $holder) is still setting up; this window continues when it finishes" }
            Start-Sleep -Seconds 2
            $waited += 2
            if ($waited -ge 3600) { Stop-Setup "ERROR_SETUP_LOCK_TIMEOUT" "another setup (pid $holder) has been running for over an hour; close its window and double-click again" }
            continue
        }
        Write-SetupLog "SETUP_LOCK_STALE removing lock left by pid $holder"
        Remove-Item -LiteralPath $LockDir -Recurse -Force -ErrorAction SilentlyContinue
        $emptyChecks = 0
    }
    [IO.File]::WriteAllText((Join-Path $LockDir "pid"), "$PID")
    $script:LockToken = "$PID"
}

function Assert-DiskSpace {
    try {
        $drive = (Get-Item -LiteralPath $Runtime).PSDrive
        if ($drive -and $drive.Free -and $drive.Free -lt $MinFreeBytes) {
            Stop-Setup "ERROR_DISK_FULL" ("about 2 GB of free disk space is needed for the first setup; found " + [int]($drive.Free / 1MB) + " MB free")
        }
    } catch { $null = $_ }  # free-space probe unavailable (for example a network share); continue without it
}

function Get-VerifiedDownload([string]$Url, [string]$Dest, [string]$Expected) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $delay = 2
    $ok = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            $client = New-Object System.Net.WebClient
            $client.DownloadFile($Url, "$Dest.part")
            Move-Item -LiteralPath "$Dest.part" -Destination $Dest -Force
            $ok = $true
            break
        } catch {
            Remove-Item -LiteralPath "$Dest.part" -Force -ErrorAction SilentlyContinue
            if ($attempt -ge 5) { Write-SetupLog ("DOWNLOAD_ERROR " + $_.Exception.Message); break }
            Write-SetupLog "WARN_DOWNLOAD_RETRY attempt=$attempt wait=$($delay)s url=$Url"
            Start-Sleep -Seconds $delay
            $delay *= 2
        }
    }
    if (-not $ok) { Stop-Setup "ERROR_DOWNLOAD_FAILED" "could not download $Url; check the internet connection and double-click again" }
    $actual = (Get-FileHash -LiteralPath $Dest -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        Remove-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
        Stop-Setup "ERROR_CHECKSUM_MISMATCH" "$Url expected=$Expected actual=$actual; the download was damaged or tampered with and was deleted"
    }
    Write-SetupLog ("CHECKSUM_OK " + ($Url.Split("/")[-1]))
}

function Expand-Zip([string]$Zip, [string]$Dest) {
    # ZipFile is much faster than Expand-Archive on Windows PowerShell 5.1 for archives with many files.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        [IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Dest)
    } catch {
        Stop-Setup "ERROR_EXTRACT_FAILED" ("$Zip " + $_.Exception.Message)
    }
}

function New-WorkDir {
    $work = Join-Path $Runtime ("tmp." + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    return $work
}

function Get-NativeOutput([string]$Exe, [string[]]$Arguments) {
    if (-not (Test-Path -LiteralPath $Exe)) { return "" }
    try {
        $output = & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { return "" }
        return ("$output").Trim()
    } catch {
        return ""
    }
}

function Install-Uv {
    $found = Get-NativeOutput $UvBin @("--version")
    if ($found -like ("uv " + $Pins["UV_VERSION"] + "*")) { Write-SetupLog ("UV_OK cached " + $Pins["UV_VERSION"]); return }
    Assert-DiskSpace
    Write-SetupLog ("==> Downloading the Python installer uv " + $Pins["UV_VERSION"] + " (one time, about 20 MB)")
    Write-ProgressPage "running" 0 "Preparing Mucaro AI" "Downloading the Python installer (one time, about 20 MB)"
    $work = New-WorkDir
    $zip = Join-Path $work "uv.zip"
    Get-VerifiedDownload ("https://github.com/astral-sh/uv/releases/download/" + $Pins["UV_VERSION"] + "/uv-x86_64-pc-windows-msvc.zip") $zip $Pins["UV_SHA256_x86_64_pc_windows_msvc"]
    $unpacked = Join-Path $work "uv"
    Expand-Zip $zip $unpacked
    if (-not (Test-Path -LiteralPath (Join-Path $unpacked "uv.exe"))) { Stop-Setup "ERROR_EXTRACT_FAILED" "uv.exe missing from archive" }
    Remove-Item -LiteralPath $UvHome -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $UvHome) | Out-Null
    Move-Item -LiteralPath $unpacked -Destination $UvHome
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    Write-SetupLog ("UV_OK installed " + (Get-NativeOutput $UvBin @("--version")))
}

function Install-Node {
    $found = Get-NativeOutput $NodeExe @("--version")
    if ($found -eq ("v" + $Pins["NODE_VERSION"])) { Write-SetupLog ("NODE_OK cached " + $Pins["NODE_VERSION"]); return }
    Assert-DiskSpace
    Write-SetupLog ("==> Downloading private Node.js " + $Pins["NODE_VERSION"] + " (one time, about 35 MB)")
    Write-ProgressPage "running" 0 "Preparing Mucaro AI" "Downloading Node.js (one time, about 35 MB)"
    $work = New-WorkDir
    $archive = "node-v" + $Pins["NODE_VERSION"] + "-win-x64"
    $zip = Join-Path $work "node.zip"
    Get-VerifiedDownload ("https://nodejs.org/dist/v" + $Pins["NODE_VERSION"] + "/" + $archive + ".zip") $zip $Pins["NODE_SHA256_win_x64"]
    Expand-Zip $zip $work
    $unpacked = Join-Path $work $archive
    if (-not (Test-Path -LiteralPath (Join-Path $unpacked "node.exe"))) { Stop-Setup "ERROR_EXTRACT_FAILED" "node.exe missing from archive" }
    Remove-Item -LiteralPath $NodeHome -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $NodeHome) | Out-Null
    Move-Item -LiteralPath $unpacked -Destination $NodeHome
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    Write-SetupLog ("NODE_OK installed " + (Get-NativeOutput $NodeExe @("--version")))
}

function Test-VenvManaged {
    $cfg = Join-Path $Root ".venv\pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $cfg)) { return $false }
    $venvHome = $null
    foreach ($line in [IO.File]::ReadAllLines($cfg)) {
        if ($line -match '^\s*home\s*=\s*(.+)$') { $venvHome = $Matches[1].Trim(); break }
    }
    if (-not $venvHome) { return $false }
    $prefix = $env:UV_PYTHON_INSTALL_DIR.TrimEnd("\") + "\"
    if (-not $venvHome.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    $found = Get-NativeOutput $VenvPy @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
    return ($found -eq $Pins["PYTHON_VERSION"])
}

function Install-Venv {
    if (Test-VenvManaged) { Write-SetupLog ("VENV_OK cached .venv python " + $Pins["PYTHON_VERSION"]); return }
    if (Test-Path -LiteralPath (Join-Path $Root ".venv")) {
        Write-SetupLog ("VENV_RECREATE .venv does not use the private Python " + $Pins["PYTHON_VERSION"] + "; rebuilding it")
    }
    Assert-DiskSpace
    Write-SetupLog ("==> Installing private Python " + $Pins["PYTHON_VERSION"] + " (one time, about 30 MB)")
    Write-ProgressPage "running" 0 "Preparing Mucaro AI" ("Installing a private Python " + $Pins["PYTHON_VERSION"] + " (one time, about 30 MB)")
    & $UvBin python install --no-bin --no-registry $Pins["PYTHON_VERSION"]
    if ($LASTEXITCODE -ne 0) { Stop-Setup "ERROR_PYTHON_INSTALL_FAILED" ("uv python install returncode=$LASTEXITCODE") }
    Remove-Item -LiteralPath (Join-Path $Root ".venv") -Recurse -Force -ErrorAction SilentlyContinue
    & $UvBin venv --seed --no-project --python $Pins["PYTHON_VERSION"] (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { Stop-Setup "ERROR_VENV_FAILED" ("uv venv returncode=$LASTEXITCODE") }
    if (-not (Test-VenvManaged)) { Stop-Setup "ERROR_VENV_FAILED" (".venv was created but does not run the private Python " + $Pins["PYTHON_VERSION"]) }
    Write-SetupLog ("VENV_OK created .venv python " + (Get-NativeOutput $VenvPy @("-c", "import platform; print(platform.python_version())")))
}

Write-SetupLog "Mucaro AI - setup and launch"
Write-SetupLog "No input is needed. The first run downloads everything it needs (2-5 minutes); later runs start in seconds."
Write-SetupLog "ONE_CLICK_STAGE0_START root=$Root log=$($script:Log)"
if ($UiMode) {
    Write-ProgressPage "running" 0 "Preparing Mucaro AI" "Checking the private Python and Node.js"
    if (-not $NoBrowser) {
        try { Start-Process -FilePath $Page } catch { Write-SetupLog ("WARN_PROGRESS_PAGE_NOT_OPENED " + $_.Exception.Message) }
    }
}

$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
if ($arch -ne "AMD64") {
    Stop-Setup "ERROR_PLATFORM_UNSUPPORTED" "Windows on $arch is not supported (the pinned rasterio publishes Windows builds for x64 only)"
}
Write-SetupLog "PLATFORM Windows $([Environment]::OSVersion.Version) $arch"

Enter-Lock
try {
    Get-ChildItem -LiteralPath $Runtime -Filter "tmp.*" -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-SetupLog "==> [0/6] Preparing private runtimes in .mucaro-runtime"
    Install-Uv
    Install-Node
    Install-Venv
} catch {
    Stop-Setup "ERROR_STAGE0_FAILED" $_.Exception.Message
}

$env:MUCARO_RUNTIME_DIR = $Runtime
$env:MUCARO_UV_BIN = $UvBin
$env:MUCARO_NODE_DIR = $NodeHome
$env:MUCARO_SETUP_LOCK = $LockDir
$env:MUCARO_SETUP_LOCK_TOKEN = $script:LockToken
$env:MUCARO_SETUP_LOG = $script:Log

# one_click.py releases the lock before it starts the app; Exit-Lock below only covers a crash.
# With --ui this process has a console without a window (Start Mucaro AI.exe starts it with CREATE_NO_WINDOW),
# and python.exe and everything it starts inherit that console, so no window ever appears.
& $VenvPy (Join-Path $Root "scripts\one_click.py") @ScriptArgs
$rc = $LASTEXITCODE
Exit-Lock
if ($UiMode -and (Test-Path -LiteralPath $Page)) {
    # one_click.py and launch_app.py leave the page ready, stopped, or in error; "running" here means a crash.
    try {
        if ([IO.File]::ReadAllText($Page).Contains('class="state-running"')) {
            Write-ProgressPage "error" 0 "Mucaro AI stopped unexpectedly" "Setup ended with code $rc before the app was ready."
        }
    } catch { $null = $_ }
}
exit $rc
