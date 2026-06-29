"""MeetingRecorder — transparent local audio recorder and transcriber.

Public entry points:

- :func:`meetingrecorder.app.run` to launch the GUI.
- :class:`meetingrecorder.audio_capture.Recorder` to capture audio to a WAV file.
- :func:`meetingrecorder.transcribe.transcribe_file` to produce a transcript.

Versioning follows semver. This is 0.x alpha; APIs may change before 1.0.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
