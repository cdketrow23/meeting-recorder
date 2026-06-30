from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from meetingrecorder import transcribe


def test_float_audio_to_pcm16_bytes_mixes_stereo_and_clips():
    audio = np.array([
        [1.0, 1.0],
        [-1.0, -1.0],
        [2.0, 2.0],
        [0.5, -0.5],
    ], dtype=np.float32)

    pcm = transcribe._float_audio_to_pcm16_bytes(audio)
    values = np.frombuffer(pcm, dtype="<i2")

    assert values.tolist() == [32767, -32767, 32767, 0]


def test_ensure_vosk_model_uses_env_path(monkeypatch, tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setenv("VOSK_MODEL", str(model))

    assert transcribe.ensure_vosk_model() == model


def test_ensure_vosk_model_raises_for_bad_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOSK_MODEL", str(tmp_path / "missing"))

    with pytest.raises(RuntimeError, match="VOSK_MODEL"):
        transcribe.ensure_vosk_model()


def test_vosk_feed_converts_numpy_to_pcm(monkeypatch, tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setenv("VOSK_MODEL", str(model))
    seen = []

    class FakeModel:
        def __init__(self, path):
            self.path = path

    class FakeRecognizer:
        def __init__(self, model, sample_rate):
            pass
        def SetWords(self, value):
            pass
        def AcceptWaveform(self, data):
            seen.append(data)
            return False
        def PartialResult(self):
            return "{}"
        def FinalResult(self):
            return '{"text":"hello world","result":[{"word":"hello","start":0,"end":0.5}]}'

    fake_vosk = types.SimpleNamespace(Model=FakeModel, KaldiRecognizer=FakeRecognizer)
    monkeypatch.setitem(sys.modules, "vosk", fake_vosk)

    vt = transcribe.VoskTranscriber(sample_rate=16000)
    vt.feed(np.array([0.5, -0.5], dtype=np.float32))
    transcript = vt.finish()

    assert seen
    assert np.frombuffer(seen[0], dtype="<i2").tolist() == [16383, -16383]
    assert transcript.segments[0].text == "hello"
