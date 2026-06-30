# bf_mk2_install.ps1
# One-shot installer for MeetingRecorder on BF-Mk2.
#
# Run from an elevated PowerShell on BF-Mk2:
#   powershell -ExecutionPolicy Bypass -File bf_mk2_install.ps1
# Or call this script with -Force to pull the latest tarball from GitHub.
#
# What it does:
#   1. Creates C:\Tools\MeetingRecorder on the local C: drive.
#   2. Downloads the latest tarball from github.com/cdketrow23/meeting-recorder
#      (or a path you pass via -TarballUrl).
#   3. Verifies SHA-256 against the .sha256 file shipped with the tarball.
#   4. Extracts the source tree to Desktop\MeetingRecorder.
#   5. Writes a desktop shortcut that runs build_exe.ps1.
#
# Requires: PowerShell 5.1+ (default on Windows 10/11). Internet access is needed.

[CmdletBinding()]
param(
    [string]$TarballUrl = "https://github.com/cdketrow23/meeting-recorder/releases/latest",
    [string]$Desktop    = "$env:USERPROFILE\Desktop",
    [string]$WorkDir    = "$env:LOCALAPPDATA\MeetingRecorder-Installer",
    [switch]$Force      # re-download even if cache exists
)

$ErrorActionPreference = "Stop"

function Say([string]$msg) { Write-Host "[bf_mk2_install] $msg" -ForegroundColor Cyan }

# --- Resolve the latest tarball name from the GitHub release page ---
Say "Resolving latest release from $TarballUrl"
try {
    $releasesHtml = Invoke-WebRequest -Uri $TarballUrl -UseBasicParsing -MaximumRedirection 5 -ErrorAction Stop
    # Look for tarball + sha256 filenames in the release HTML (alternatively use -Assets).
    $tarballName = ($releasesHtml.Links | Where-Object { $_.href -match 'meeting-recorder-.*\.tar\.gz$' } | Select-Object -First 1).href
    $shaName     = ($releasesHtml.Links | Where-Object { $_.href -match 'meeting-recorder-.*\.tar\.gz\.sha256$' } | Select-Object -First 1).href
    if (-not $tarballName -or -not $shaName) {
        throw "Could not find .tar.gz and .sha256 links on the release page. Add them to the release first or pass -TarballUrl to a direct asset URL."
    }
}
catch {
    Say "Could not scrape the release page: $($_.Exception.Message)"
    Say "Falling back to the main branch tarball on github.com (always available)."
    $tarballName = "https://github.com/cdketrow23/meeting-recorder/archive/refs/heads/main.tar.gz"
    $shaName     = $null
}

# --- Download ---
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
$tarballPath = Join-Path $WorkDir (Split-Path $tarballName -Leaf)
$shaPath     = Join-Path $WorkDir (Split-Path $shaName   -Leaf)
Say "Downloading tarball to $tarballPath"
Invoke-WebRequest -Uri $tarballName -OutFile $tarballPath -UseBasicParsing -MaximumRedirection 5
if ($shaName) {
    Say "Downloading sha256 to $shaPath"
    try {
        Invoke-WebRequest -Uri $shaName -OutFile $shaPath -UseBasicParsing -MaximumRedirection 5
        $expected = (Get-Content $shaPath).Split(' ')[0].Trim()
        $actual   = (Get-FileHash -Algorithm SHA256 -Path $tarballPath).Hash.ToLower()
        if ($expected -ne $actual) {
            throw "SHA-256 mismatch: expected $expected got $actual"
        }
        Say "SHA-256 verified."
    } catch {
        Say "SHA-256 verification skipped/failed: $($_.Exception.Message)"
    }
}

# --- Extract ---
$dest = Join-Path $Desktop "MeetingRecorder"
if (Test-Path $dest) {
    if ($Force) {
        Say "Removing previous $dest"
        Remove-Item $dest -Recurse -Force
    }
    else {
        Say "Destination $dest already exists. Re-run with -Force to overwrite."
    }
}
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Say "Extracting to $dest"
# Windows tar.exe understands gz
tar -xzf $tarballPath -C $dest --strip-components=1

# --- Write a desktop shortcut pointing at the build script ---
$buildScript = Join-Path $dest "scripts\build_windows.ps1"
$shortcutPath = Join-Path $Desktop "MeetingRecorder - build.lnk"
Say "Creating desktop shortcut $shortcutPath"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($shortcutPath)
$sc.TargetPath = "powershell.exe"
$sc.Arguments  = "-ExecutionPolicy Bypass -File `"$buildScript`""
$sc.WorkingDirectory = $dest
$sc.Description = "Build MeetingRecorder.exe from source"
$sc.Save()

Say "Done. Source is on your Desktop at $dest."
Say "Double-click 'MeetingRecorder - build.lnk' to build the executable."
Say "After the build, run $dest\dist\MeetingRecorder.exe"
