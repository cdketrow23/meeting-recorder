from __future__ import annotations

import datetime
from pathlib import Path

from meetingrecorder import io_utils


def test_utc_timestamp_format():
    n = datetime.datetime(2026, 6, 29, 9, 14, 22)
    assert io_utils.utc_timestamp(n) == "2026-06-29T09-14-22Z"


def test_local_date_hhmm_uses_24h_system_time_without_seconds():
    n = datetime.datetime(2026, 6, 30, 21, 42, 59)
    assert io_utils.local_date_hhmm(n) == "2026-06-30_2142"


def test_sanitize_filename_strips_invalid_chars_and_normalizes_spaces():
    assert io_utils.sanitize_filename("a/b:c*d?e") == "a_b_c_d_e"
    assert io_utils.sanitize_filename("   ...") == "recording"
    assert io_utils.sanitize_filename("CON") == "_CON"
    assert io_utils.sanitize_filename("normal file") == "normal_file"


def test_session_basename_uses_meeting_name_local_date_and_hhmm():
    b = io_utils.session_basename(
        prefix="Weekly Sales Sync",
        now=datetime.datetime(2026, 6, 30, 21, 42, 59),
    )
    assert b == "Weekly_Sales_Sync_2026-06-30_2142"


def test_session_paths_returns_expected_keys(tmp_path):
    p = io_utils.session_paths(tmp_path, "meeting_x")
    assert p["wav"].name == "meeting_x.wav"
    assert p["transcript_txt"].name == "meeting_x_transcript.txt"
    assert p["transcript_md"].name == "meeting_x_transcript.md"
    assert p["transcript_srt"].name == "meeting_x_transcript.srt"
    assert p["transcript_json"].name == "meeting_x_transcript.json"
    assert p["metadata"].name == "meeting_x_metadata.json"
    assert p["raw_dir"] == tmp_path / "raw" / "meeting_x"


def test_default_output_dir_creates_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    out = io_utils.default_output_dir()
    assert out.exists()
    assert out.name == "MeetingRecorder"
