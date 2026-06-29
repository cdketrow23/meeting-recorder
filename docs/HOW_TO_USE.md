# How to use MeetingRecorder

MeetingRecorder is a small, transparent Windows desktop app. It records this computer's microphone and system audio (anything the speakers would play) into a single WAV file, then produces a transcript next to it. The window shows a visible "Recording" indicator while it's running. Use it for meetings you are part of and where participants have been informed that recording will happen.

## 1. Quick start

1. **Get the executable** — download `MeetingRecorder.exe` from
   *GitHub Releases* → `https://github.com/cdketrow23/meeting-recorder/releases/latest`,
   *or* clone this repo and build it yourself (see §5).
2. **Run it.** Double-click `MeetingRecorder.exe`. You do *not* need to install Python or any extra software.
3. **Pick an output folder.** First launch uses `Downloads\MeetingRecorder`. Change it any time via **Choose...**; the choice is remembered.
4. **Choose what to capture.** Tick *Microphone* and/or *System audio (WASAPI loopback)*. At least one must be on.
5. **Click Record.** A red bar appears, the window title says `MeetingRecorder — ● Recording`, and a status badge stays at the top of the window while audio is being captured.
6. **Click Stop.** Within a few seconds the app finalizes the WAV and creates the transcripts.
7. **Click Open output folder** to view your files.

> Tip: the window title, the red badge in the app, and the saved WAV file are all visible indicators that recording is in progress. There is no "stealth" mode — the app does not hide, run in the background, or launch on a timer.

## 2. File layout per session

Each recording produces the same five files, named with the timestamp of when you pressed Record:

```
meeting_2026-06-29T09-14-22Z.wav                  # the audio
meeting_2026-06-29T09-14-22Z.transcript.txt       # plain-text transcript with [HH:MM:SS] timestamps
meeting_2026-06-29T09-14-22Z.transcript.md        # Markdown version, easy to paste into notes
meeting_2026-06-29T09-14-22Z.transcript.srt       # SubRip, opens in VLC / MPV for clickable playback
meeting_2026-06-29T09-14-22Z.metadata.json        # engine, language, segment list in JSON
```

The timestamps in the transcript are **wall-clock time from the moment you pressed Record**, so `[00:01:25]` means one minute and twenty-five seconds into the session.

## 3. Capturing "what the other people are saying"

Windows treats the microphone and the speakers' output as different audio streams. To record both yourself **and** a meeting on Zoom / Teams / Google Meet without enabling "admit a recording bot":

| Approach | How | When to pick it |
|---|---|---|
| **Speaker + Mic setup with VoiceMeeter** | Install [VoiceMeeter Banana](https://vb-audio.com/Voicemeeter/banana/). Configure your conferencing app to use *VoiceMeeter Output* as its speaker. Select VoiceMeeter in MeetingRecorder's system-audio source. | Best quality. Lets you dial a mix per channel. |
| **Stereo Mix / "What U Hear"** | Right-click the speaker → *Sound settings* → *More sound settings* → *Playback* → enable **Stereo Mix** (some sound cards call it "What U Hear") and set as default. Restart the meeting app. | Built-in option, no extra software. Quality varies by sound card. |
| **Mic only** | Untick *System audio*. Only the local microphone is captured. | When you only want your own audio, e.g. for voice notes. |

If system-audio capture is unavailable on your hardware (no Stereo Mix, no WASAPI loopback), MeetingRecorder shows a message and keeps working in mic-only mode.

## 4. Transcription backends

The app ships with **Vosk** for offline transcription — small English model, ~50 MB, fully on this computer, no network calls. You download the model once:

1. Grab `vosk-model-small-en-us-0.15` from <https://alphacephei.com/vosk/models>.
2. Unpack it into a folder named `vosk-model` in the *same directory as MeetingRecorder.exe*. (You can set the `VOSK_MODEL` environment variable to any folder instead.)
3. First launch will pick it up automatically.

If you'd rather use a higher-quality model, install the optional backend:

```powershell
pip install -r requirements-optional.txt
# Now select "whisper" in the Backend dropdown. First run downloads the model (~75 MB for small.en).
```

MeetingRecorder automatically picks the best backend it finds at runtime. If neither is installed, you still get a valid WAV file with an empty transcript.

## 5. Building the executable yourself

You'll need a Windows machine with Python 3.10+ and the build toolchain:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pyinstaller meetingrecorder.spec --noconfirm
# → dist\MeetingRecorder.exe
```

To produce a fresh build for distribution:

```powershell
# Tag a release, push the tag. The GitHub Actions workflow at
# .github/workflows/build-windows.yml builds the .exe and attaches it
# to the matching GitHub Release.
git tag v0.1.0
git push origin v0.1.0
```

## 6. Privacy and your legal responsibility

This tool only records audio on the computer where it is installed, only while you have clicked **Record**, and only for the duration until you click **Stop**. It does not phone home, transmit your audio to anyone, or bypass platform-specific consent dialogs.

**You** are responsible for using it lawfully. Many jurisdictions require that all participants be informed before a call is recorded (one-party / two-party consent rules). Common-sense examples:

- ✅ Recording a meeting you organized where the agenda already stated the call would be recorded for accuracy.
- ✅ Recording your own voice notes for personal use.
- ❌ Recording a conversation you are not part of, or where the other party has a reasonable expectation of privacy.

When in doubt, announce at the top of the call: *"This call is being recorded for note-taking purposes."*

The full MIT license notice in `LICENSE` includes the same disclaimer.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Recording stops immediately, *system audio* greyed out | Loopback disabled on this machine | Toggle Stereo Mix (see §3) or pick the mic-only source |
| Final transcript is empty | No speech detected, or no backend installed | Re-check mic/speaker routing; install Vosk model per §4 |
| "Sample rate X not supported" | Selected rate too low/high for this hardware | Stay on the default 16 kHz; switch back if you changed it |
| Long meetings warn about file size | 16-bit mono at 16 kHz ≈ 32 KB/s; a 3-hour file ≈ 345 MB | Reduce sample rate in Settings or rotate to a fresh session every 2 hours |

## 8. Republish / repo locations

If you fork this project:

- `scripts/publish.py` — robust one-shot push with a rate-limit fallback (see `docs/RATE_LIMIT.md`).
- `docs/RATE_LIMIT.md` — what happens when GitHub is unavailable or throttled; the script produces a local tarball + SHA-256 manifest at `dist/` and mirrors it to NAS shares.

End of document.
