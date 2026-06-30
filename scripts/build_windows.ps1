# build_windows.ps1
# Build MeetingRecorder.exe from source on a Windows machine.
#
# Run from an elevated PowerShell on BF-Mk2:
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
# Produces dist\MeetingRecorder.exe and dist\MeetingRecorder.exe.sha256.
# Works on Windows 10/11 with Python 3.10+ installed from python.org.
# No Visual Studio or other toolchain needed.

[CmdletBinding()]
param(
    [string]$Python = "py -3.11"   # the launcher command from python.org installs
)

$ErrorActionPreference = "Stop"
function Say([string]$msg) { Write-Host "[build_windows] $msg" -ForegroundColor Cyan }

# --- Resolve repo root ---
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $repoRoot
Say "Repo root: $repoRoot"

# --- Venv + deps ---
$venv = Join-Path $repoRoot ".venv"
if (-not (Test-Path $venv)) {
    Say "Creating virtualenv at $venv"
    & $Python -m venv $venv
}
$pythonExe = Join-Path $venv "Scripts\python.exe"
& $pythonExe -m pip install --upgrade pip wheel | Out-Null
Say "Installing requirements"
& $pythonExe -m pip install -r requirements.txt
& $pythonExe -m pip install pyinstaller==6.10.0

# --- Tests ---
Say "Running tests"
& $pythonExe -m pytest -q tests/

# --- Build ---
Say "Building executable via PyInstaller"
& $pythonExe -m PyInstaller meetingrecorder.spec --noconfirm

# --- Hash for distribution ---
$exe = Join-Path $repoRoot "dist\MeetingRecorder.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe not found"
}
$sha = (Get-FileHash -Algorithm SHA256 -Path $exe).Hash
$hex = "$sha  MeetingRecorder.exe"
$hexPath = "$exe.sha256"
Set-Content -Path $hexPath -Value $hex -NoNewline
& (Get-Process -Id $PID).Path /C "where.exe $exe" | Out-Null
(Get-Item $exe).Length | ForEach-Object { Say "Output: $exe ($([math]::Round($_/1MB,1)) MB)" }
Say "SHA-256: $sha"
Say "Done. Run: `"$exe`""
