# Windows entry point for "Mucaro AI.exe" (the counterpart of scripts/native_start.sh on macOS).
#
# Runs the double-click setup with no console window and no browser: Mucaro AI opens in its own
# window (scripts/native_window.py). All output goes to .mucaro-runtime\logs\launch-*.log. If the
# setup or the app cannot start, one dialog names the exit code and the log.
#
# Keep this file ASCII only: Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.
# Native commands are never run with 2>&1 (PowerShell 5.1 turns their first stderr line into a
# terminating error); the child's output is redirected by Start-Process instead.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
# launch_app.py returns 11 when the Microsoft Edge WebView2 Runtime that draws the window is missing.
$WebView2MissingCode = 11
$WebView2Download = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

function Show-Failure([string]$Message) {
    # MUCARO_DIALOG_LOG lets tests record the dialog instead of showing it.
    if ($env:MUCARO_DIALOG_LOG) {
        try { [IO.File]::AppendAllText($env:MUCARO_DIALOG_LOG, $Message + "`r`n") } catch { $null = $_ }
        return
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($Message, 0, "Mucaro AI", 48)
    } catch { $null = $_ }
}

function ConvertTo-Argument([string]$Value) {
    # Start-Process joins arguments with spaces, so quote any that contain spaces or quotes.
    if ($Value -match '[\s"]') { return '"' + ($Value -replace '"', '\"') + '"' }
    return $Value
}

$LogDir = Join-Path $Root ".mucaro-runtime\logs"
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
} catch {
    Show-Failure "Mucaro AI cannot write to its folder. Move the entire mucaro-ai folder somewhere writable and open Mucaro AI.exe again."
    exit 2
}
$Stamp = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $PID
$Log = Join-Path $LogDir ("launch-" + $Stamp + ".log")
$ErrLog = Join-Path $LogDir ("launch-" + $Stamp + ".err.log")

$env:MUCARO_SILENT_LAUNCH = "1"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Arguments = @(
    "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    "-File", (ConvertTo-Argument (Join-Path $Root "scripts\one_click_windows.ps1")), "--native"
)
foreach ($arg in $args) { $Arguments += (ConvertTo-Argument ([string]$arg)) }

try {
    $child = Start-Process -FilePath $PowerShell -ArgumentList $Arguments -WorkingDirectory $Root -NoNewWindow -PassThru `
        -RedirectStandardOutput $Log -RedirectStandardError $ErrLog
    # Reading Handle before the process ends makes ExitCode reliable in Windows PowerShell 5.1.
    $null = $child.Handle
    $child.WaitForExit()
    $code = $child.ExitCode
    if ($null -eq $code) { $code = 1 }
} catch {
    Show-Failure ("Mucaro AI could not start Windows PowerShell: " + $_.Exception.Message)
    exit 1
}

if ($code -eq $WebView2MissingCode) {
    Show-Failure ("Mucaro AI needs the Microsoft Edge WebView2 Runtime to show its window. Install it from " + $WebView2Download + ", then open Mucaro AI.exe again. Details are in: " + $Log)
} elseif ($code -ne 0) {
    Show-Failure ("Mucaro AI could not start (code " + $code + "). Details are in: " + $Log + ". Open Mucaro AI.exe again to retry; setup resumes where it stopped.")
}
exit $code
