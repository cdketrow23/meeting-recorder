from __future__ import annotations

import json
from pathlib import Path

import pytest

from meetingrecorder.transcript_format import (
    Segment,
    Transcript,
    to_markdown,
    to_srt,
    to_txt,
    write_transcript,
)


@pytest.fixture()
def transcript():
    return Transcript(
        segments=[
            Segment(0.0, 2.5, "Hello everyone."),
            Segment(2.5, 5.0, "Welcome to the meeting.", speaker="Alice"),
        ],
        language="en",
        engine="vosk",
    )


def test_to_txt_includes_timestamp_and_speaker(transcript):
    out = to_txt(transcript)
    assert "[00:00:00] Hello everyone." in out
    assert "[00:00:02] Alice: Welcome to the meeting." in out


def test_to_markdown_bold_timestamps(transcript):
    md = to_markdown(transcript)
    assert "# Transcript" in md
    assert "**Alice**" in md
    assert "`00:02`" in md


def test_to_srt_timecode(transcript):
    srt = to_srt(transcript)
    assert "1\n00:00:00,000 --> 00:00:02,500\nHello everyone." in srt
    assert "Alice: Welcome to the meeting." in srt


def test_to_srt_handles_empty():
    assert to_srt(Transcript()) == ""
    assert to_txt(Transcript()) == ""
    assert "_(no speech detected)_" in to_markdown(Transcript())


def test_write_transcript_produces_files(transcript, tmp_path):
    paths = {
        "transcript_txt": tmp_path / "x.transcript.txt",
        "transcript_md": tmp_path / "x.transcript.md",
        "transcript_srt": tmp_path / "x.transcript.srt",
        "metadata": tmp_path / "x.metadata.json",
    }
    written = write_transcript(transcript, paths)
    assert "transcript_txt" in written
    assert paths["transcript_txt"].is_file()
    meta = json.loads(paths["metadata"].read_text())
    assert meta["engine"] == "vosk"
    assert len(meta["segments"]) == 2


def test_segment_duration_zero_for_invalid_window():
    s = Segment(5.0, 3.0, "backwards")
    assert s.duration == 0.0


def test_to_srt_min_duration_50ms():
    t = Transcript(segments=[Segment(0.0, 0.0, "ping")])
    srt = to_srt(t)
    assert "00:00:00,000 --> 00:00:00,050" in srt
