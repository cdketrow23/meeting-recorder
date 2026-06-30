"""Filename and path helpers — pure functions, fully unit-testable on any OS.

The app creates a Windows-safe final WAV under ``<output_dir>/<basename>.wav``.
For named meetings, basenames look like ``Weekly_Sync_2026-06-30_2142``:
user-provided meeting name + system date + 24-hour HHMM start time.
Transcripts use the exact same base name plus ``_transcript`` before the
extension.
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
_WHITESPACE = re.compile(r"\s+")
_UNDERSCORES = re.compile(r"_+")


def utc_timestamp(now: _dt.datetime | None = None) -> str:
    """Return ``YYYY-MM-DDTHH-MM-SSZ`` suitable for filenames anywhere on earth."""
    n = (now or _dt.datetime.utcnow()).replace(microsecond=0)
    return n.strftime("%Y-%m-%dT%H-%M-%SZ")


def local_date_hhmm(now: _dt.datetime | None = None) -> str:
    """Return local/system ``YYYY-MM-DD_HHMM`` with no seconds."""
    n = (now or _dt.datetime.now()).replace(second=0, microsecond=0)
    return n.strftime("%Y-%m-%d_%H%M")


def sanitize_filename(name: str) -> str:
    """Replace filesystem-hostile characters with ``_`` and trim trailing dots/spaces.

    Empty strings and Windows reserved names are mapped to a safe fallback.
    The result is suitable for any modern OS filesystem.
    """
    cleaned = _INVALID_FS_CHARS.sub("_", name)
    cleaned = _WHITESPACE.sub("_", cleaned).strip().strip(".")
    cleaned = _UNDERSCORES.sub("_", cleaned).strip("_")
    if not cleaned:
        cleaned = "recording"
    if cleaned.upper().split(".")[0] in _RESERVED_WIN_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:120]


def default_output_dir(user_home: Path | None = None) -> Path:
    """``Downloads/MeetingRecorder`` if it exists or can be created, else ``~``."""
    home = Path(user_home) if user_home is not None else Path.home()
    candidates = [
        Path.home() / "Downloads" / "MeetingRecorder",
        home / "Downloads" / "MeetingRecorder",
        home,
    ]
    seen: set[Path] = set()
    deduped: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    for c in deduped:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".mr_writeprobe"
            probe.write_text("ok")
            probe.unlink()
            return c.resolve()
        except OSError:
            continue
    import tempfile

    return Path(tempfile.gettempdir()).resolve()


def session_basename(prefix: str = "meeting", now: _dt.datetime | None = None) -> str:
    """Produce ``<safe name>_YYYY-MM-DD_HHMM`` using local/system time."""
    return sanitize_filename(f"{prefix}_{local_date_hhmm(now)}")


def session_paths(output_dir: Path, basename: str) -> dict[str, Path]:
    """Return the canonical paths produced for one recording session."""
    output_dir = Path(output_dir)
    return {
        "wav": output_dir / f"{basename}.wav",
        "transcript_txt": output_dir / f"{basename}_transcript.txt",
        "transcript_md": output_dir / f"{basename}_transcript.md",
        "transcript_srt": output_dir / f"{basename}_transcript.srt",
        "transcript_json": output_dir / f"{basename}_transcript.json",
        "metadata": output_dir / f"{basename}_metadata.json",
        "raw_dir": output_dir / "raw" / basename,
    }
