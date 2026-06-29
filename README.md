# MeetingRecorder — Windows desktop app

A small, transparent Windows desktop application that records this computer's microphone and system audio, then produces a timestamped transcript alongside the audio file. Intended for use on meetings the operator has the right to record and where participants have been informed.

## What it does

- Captures microphone + system audio (stereo mix / WASAPI loopback) into a single normalized WAV file
- Shows a visible recording state in the window title and a red status badge while recording
- Produces a transcript in `.txt`, `.md`, and `.srt` (timestamps). SRT uses wall-clock time relative to recording start
- Lets the user pick any output folder; defaults to `Downloads/MeetingRecorder/<YYYY-MM-DD>/`
- Uses local transcription first (Vosk small English model, fully offline); optional faster-whisper backend if installed
- Comes packaged as a Windows `.exe` (PyInstaller onefile) so no Python install needed on the target machine

## What it does not do

- It does **not** run hidden, on a schedule, or in the background
- It does not silence or modify any other application's behavior
- It does not access the network during recording (transcription runs locally)
- It does not bypasses platform consent dialogs or screen/recording policies — system-audio capture on Windows relies on your usual system settings (Stereo Mix enabled or "What U Hear" type routing)

## Required Windows setup for system-audio

Windows separates microphone and "what the speakers are playing." Two reliable options:

1. **Stereo Mix / WASAPI loopback** (recommended):
   - Right-click the speaker icon → *Sound settings* → *More sound settings* → on the *Playback* tab enable **Stereo Mix** (or "What U Hear") and set it as default.
   - Or use any virtual-audio cable (VB-Cable, VoiceMeeter). VoiceMeeter Banana is the most reliable for long meetings.

2. **Mic only**: works out of the box. The system-audio track will be silent.

## Privacy reminder

This tool records audio on the operator's machine at the operator's request. Confirm participants in any meeting you record are informed that recording is happening, where required by law. The app surfaces a "Recording" indicator while running by design.

## Running from source on Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m meetingrecorder
```

## Building the Windows .exe

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pyinstaller meetingrecorder.spec --noconfirm
```

The one-file executable is at `dist\MeetingRecorder.exe`.

## Quick start

1. Launch `MeetingRecorder.exe`.
2. Click **Choose output folder** (defaults to `Downloads\MeetingRecorder`).
3. Pick which sources to capture (mic, system audio, or both).
4. Click **Record** — the window title and badge show "● Recording".
5. Click **Stop** — the app finalizes the WAV and produces a transcript next to it.

See `docs/HOW_TO_USE.md` for the full walkthrough, file layouts, troubleshooting, and the optional Whisper backend.
