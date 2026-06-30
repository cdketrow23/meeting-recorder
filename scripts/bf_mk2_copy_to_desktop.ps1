# bf_mk2_copy_to_desktop.ps1
# On BF-Mk2, copies the latest meeting-recorder tarball (and installer
# scripts) from the NAS share into the current user's Desktop folder,
# then runs the installer to build the .exe in-place.
#
# Run from an elevated PowerShell on BF-Mk2:
#   powershell -ExecutionPolicy Bypass -File bf_mk2_copy_to_desktop.ps1
#
# Carson typically has these NAS shares mapped:
#   - \\192.168.0.117\ketrow-family on the home Windows host
# If your mapping letter differs, set -NasRoot.

[CmdletBinding()]
param(
    [string]$NasRoot = '\\192.168.0.117\ketrow-family\Neo Infrastructure\meeting-recorder',
    [string]$Desktop = "$env:USERPROFILE\Desktop"
)

$ErrorActionPreference = "Stop"
function Say([string]$msg) { Write-Host "[bf_mk2_copy] $msg" -ForegroundColor Cyan }

# Pick the latest tarball
$latestTarball = Get-ChildItem -Path $NasRoot -Filter 'meeting-recorder-*.tar.gz' | Sort-Object Name | Select-Object -Last 1
if (-not $latestTarball) {
    throw "No meeting-recorder tarballs at $NasRoot. Pull from GitHub instead."
}
$latestSha = $latestTarball.FullName + '.sha256'
if (Test-Path $latestSha) {
    $expected = (Get-Content $latestSha).Split(' ')[0].Trim()
    $actual   = (Get-FileHash -Algorithm SHA256 -Path $latestTarball.FullName).Hash.ToLower()
    if ($expected -ne $actual) {
        throw "SHA-256 mismatch for $($latestTarball.Name): expected $expected got $actual"
    }
    Say "SHA-256 verified."
}

# Extract to Desktop\MeetingRecorder
$dest = Join-Path $Desktop "MeetingRecorder"
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Say "Extracting $($latestTarball.Name) to $dest"
tar -xzf $latestTarball.FullName -C $dest --strip-components=1

# Drop a copy of bf_mk2_install.ps1 (so the user can re-pull later)
Copy-Item -Path (Join-Path $dest 'scripts\bf_mk2_install.ps1') -Destination (Join-Path $Desktop 'bf_mk2_install.ps1') -Force

Say "Done."
Say "Source on Desktop at: $dest"
Say "Run: powershell -ExecutionPolicy Bypass -File `"$dest\scripts\build_windows.ps1`""
Say "After the build, run: `"$dest\dist\MeetingRecorder.exe`""
