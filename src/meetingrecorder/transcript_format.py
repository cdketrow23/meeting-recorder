"""Transcript segment representation and writers.

A :class:`Segment` is one chunk of speech with an inclusive start and end
timestamp in seconds since recording start. The writers below render the
same list of segments to plain text, Markdown, and SubRip SRT.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Segment:
    """A single transcript segment with optional speaker label."""

    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)
    language: str = "en"
    engine: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "engine": self.engine,
            "segments": [asdict(s) for s in self.segments],
        }


# -------------------- format helpers --------------------

def _fmt_srt_time(seconds: float) -> str:
    """``00:01:23,456`` SRT timecode."""
    if seconds < 0:
        seconds = 0
    total_ms = int(round(seconds * 1000))
    hh, rem_ms = divmod(total_ms, 3_600_000)
    mm, rem_ms = divmod(rem_ms, 60_000)
    ss, ms = divmod(rem_ms, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def to_txt(transcript: Transcript) -> str:
    """Plain text with ``[HH:MM:SS]`` timestamps and optional ``Speaker:`` labels."""
    lines = []
    for s in transcript.segments:
        hh, rem_s = divmod(int(s.start), 3600)
        mm, ss = divmod(rem_s, 60)
        stamp = f"[{hh:02d}:{mm:02d}:{ss:02d}]"
        head = f"{s.speaker}: " if s.speaker else ""
        lines.append(f"{stamp} {head}{s.text}".rstrip())
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def to_markdown(transcript: Transcript) -> str:
    """Markdown list, one ``Segment`` per item, with bold timestamps."""
    out = [f"# Transcript (engine={transcript.engine}, language={transcript.language})", ""]
    for s in transcript.segments:
        start = f"{int(s.start // 60):02d}:{int(s.start % 60):02d}"
        label = f" **{s.speaker}**:" if s.speaker else ""
        out.append(f"- `{start}`{label} {s.text}")
    if len(out) == 2:
        out.append("_(no speech detected)_")
    return "\n".join(out) + "\n"


def to_srt(transcript: Transcript) -> str:
    """SubRip subtitle format."""
    blocks = []
    for i, s in enumerate(transcript.segments, 1):
        start = _fmt_srt_time(s.start)
        end = _fmt_srt_time(max(s.end, s.start + 0.05))
        speaker = f"{s.speaker}: " if s.speaker else ""
        blocks.append(f"{i}\n{start} --> {end}\n{speaker}{s.text}\n")
    if not blocks:
        return ""
    return "\n".join(blocks)


# -------------------- io --------------------

def write_transcript(transcript: Transcript, paths: dict[str, Path]) -> dict[str, Path]:
    """Write the transcript in all three formats plus JSON metadata.

    ``paths`` is the dict returned by :func:`meetingrecorder.io_utils.session_paths`.
    Returns a dict mapping format name to the actually-written path.
    """
    written: dict[str, Path] = {}
    payloads = {
        "transcript_txt": to_txt(transcript),
        "transcript_md": to_markdown(transcript),
        "transcript_srt": to_srt(transcript),
    }
    for key, text in payloads.items():
        if key not in paths:
            continue
        p = Path(paths[key])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        written[key] = p
    if "metadata" in paths:
        mp = Path(paths["metadata"])
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(transcript.to_dict(), indent=2), encoding="utf-8")
        written["metadata"] = mp
    return written


def iter_segments(transcript: Transcript) -> Iterable[Segment]:
    yield from transcript.segments
