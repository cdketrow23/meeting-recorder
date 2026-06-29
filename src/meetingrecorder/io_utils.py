"""Filename and path helpers — pure functions, fully unit-testable on any OS.

The audio capture layer writes a single WAV under
``<output_dir>/<basename>.wav`` where basename is like ``meeting_2026-06-29_09-14-22``.
After recording stops, the transcription step produces a transcript file with
the same stem plus ``.transcript.{txt,md,srt}``.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WIN_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def utc_timestamp(now: _dt.datetime | None = None) -> str:
    """Return ``YYYY-MM-DDTHH-MM-SSZ`` suitable for filenames anywhere on earth."""
    n = (now or _dt.datetime.utcnow()).replace(microsecond=0)
    return n.strftime("%Y-%m-%dT%H-%M-%SZ")


def sanitize_filename(name: str) -> str:
    """Replace filesystem-hostile characters with ``_`` and trim trailing dots/spaces.

    Empty strings and Windows reserved names are mapped to a safe fallback.
    The result is suitable for any modern OS filesystem.
    """
    cleaned = _INVALID_FS_CHARS.sub("_", name).strip().strip(".")
    if not cleaned:
        cleaned = "recording"
    if cleaned.upper().split(".")[0] in _RESERVED_WIN_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:120]  # keep paths sane on every platform


def default_output_dir(user_home: Path | None = None) -> Path:
    """``Downloads/MeetingRecorder`` if it exists or can be created, else ``~``.

    Lookup order:
        1. ``$USERPROFILE/Downloads/MeetingRecorder`` (Windows)
        2. ``~/Downloads/MeetingRecorder`` (macOS, Linux fallback)
        3. ``~`` as last resort
    """
    home = Path(user_home) if user_home is not None else Path.home()
    candidates = [
        Path.home() / "Downloads" / "MeetingRecorder",
        home / "Downloads" / "MeetingRecorder",
        home,
    ]
    # De-dup while preserving order
    seen: set[Path] = set()
    deduped = [c for c in candidates if not (c in seen or seen.add(c))]
    for c in deduped:
        try:
            c.mkdir(parents=True, exist_ok=True)
            # Probe writability with a tiny throwaway file
            probe = c / ".mr_writeprobe"
            probe.write_text("ok")
            probe.unlink()
            return c.resolve()
        except OSError:
            continue
    # Last-ditch: tempdir
    import tempfile

    return Path(tempfile.gettempdir()).resolve()


def session_basename(prefix: str = "meeting", now: _dt.datetime | None = None) -> str:
    """Produce a unique sortable stem like ``meeting_2026-06-29_09-14-22``."""
    return sanitize_filename(f"{prefix}_{utc_timestamp(now)}")


def session_paths(output_dir: Path, basename: str) -> dict[str, Path]:
    """Return the canonical paths produced for one recording session."""
    output_dir = Path(output_dir)
    return {
        "wav": output_dir / f"{basename}.wav",
        "transcript_txt": output_dir / f"{basename}.transcript.txt",
        "transcript_md": output_dir / f"{basename}.transcript.md",
        "transcript_srt": output_dir / f"{basename}.transcript.srt",
        "metadata": output_dir / f"{basename}.metadata.json",
    }
