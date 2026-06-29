from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from meetingrecorder import io_utils


def test_utc_timestamp_format():
    n = datetime.datetime(2026, 6, 29, 9, 14, 22)
    assert io_utils.utc_timestamp(n) == "2026-06-29T09-14-22Z"


def test_sanitize_filename_strips_invalid_chars():
    assert io_utils.sanitize_filename("a/b:c*d?e") == "a_b_c_d_e"
    assert io_utils.sanitize_filename("   ...") == "recording"
    assert io_utils.sanitize_filename("CON") == "_CON"
    assert io_utils.sanitize_filename("normal file") == "normal file"


def test_session_basename_is_unique_and_sortable():
    b1 = io_utils.session_basename(prefix="meeting", now=datetime.datetime(2026, 6, 29, 9, 14, 22))
    b2 = io_utils.session_basename(prefix="meeting", now=datetime.datetime(2026, 6, 29, 9, 14, 23))
    assert b1 == "meeting_2026-06-29T09-14-22Z"
    assert b2 == "meeting_2026-06-29T09-14-23Z"
    assert b1 < b2


def test_session_paths_returns_expected_keys(tmp_path):
    p = io_utils.session_paths(tmp_path, "meeting_x")
    assert p["wav"].name == "meeting_x.wav"
    assert p["transcript_txt"].name == "meeting_x.transcript.txt"
    assert p["transcript_md"].name == "meeting_x.transcript.md"
    assert p["transcript_srt"].name == "meeting_x.transcript.srt"
    assert p["metadata"].name == "meeting_x.metadata.json"


def test_default_output_dir_creates_dir(tmp_path, monkeypatch):
    # Pretend the user has a Downloads folder under tmp_path/home
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    out = io_utils.default_output_dir()
    assert out.exists()
    assert out.name == "MeetingRecorder"
