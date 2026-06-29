"""Smoke tests for package importability and module-level behavior."""

from __future__ import annotations

import importlib

import pytest


def test_version_is_string():
    pkg = importlib.import_module("meetingrecorder")
    assert isinstance(pkg.__version__, str)
    parts = pkg.__version__.split(".")
    assert len(parts) >= 2


def test_submodules_importable():
    for name in (
        "meetingrecorder.io_utils",
        "meetingrecorder.transcript_format",
        "meetingrecorder.audio_capture",
        "meetingrecorder.transcribe",
        "meetingrecorder.app",
    ):
        importlib.import_module(name)


def test_audio_capture_recorderconfig_rejects_bad_rate():
    from meetingrecorder.audio_capture import AudioError, RecorderConfig

    with pytest.raises(AudioError):
        RecorderConfig(sample_rate=1000)
    with pytest.raises(AudioError):
        RecorderConfig(channels=7)
    with pytest.raises(AudioError):
        RecorderConfig(chunk_seconds=-1)


def test_app_run_is_a_callable():
    from meetingrecorder import app

    assert callable(app.run)


def test_main_entry_point_exists():
    from meetingrecorder.__main__ import main

    assert callable(main)
