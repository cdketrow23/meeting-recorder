from __future__ import annotations

import sys
import types
from collections import deque

import numpy as np
import pytest

from meetingrecorder import audio_capture


def _patch_sounddevice(monkeypatch, hostapis, devices):
    created = []

    def fake_input_stream(**kwargs):
        created.append(kwargs)
        return types.SimpleNamespace(start=lambda: None, stop=lambda: None, close=lambda: None)

    monkeypatch.setattr(audio_capture.sd, "query_hostapis", lambda: hostapis)
    monkeypatch.setattr(audio_capture.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(audio_capture.sd, "InputStream", fake_input_stream)
    return created


def _patch_soundcard(monkeypatch, speaker_name="Speakers", loopback_name="Speakers"):
    speaker = types.SimpleNamespace(name=speaker_name)
    loopback = types.SimpleNamespace(name=loopback_name)
    fake = types.SimpleNamespace(
        default_speaker=lambda: speaker,
        get_microphone=lambda name, include_loopback=False: loopback,
        all_microphones=lambda include_loopback=False: [loopback],
    )
    monkeypatch.setitem(sys.modules, "soundcard", fake)
    return speaker, loopback


def test_wasapi_output_device_prefers_default_speaker(monkeypatch):
    hostapis = [
        {"name": "MME", "default_output_device": 0},
        {"name": "Windows WASAPI", "default_output_device": 2},
    ]
    devices = [
        {"name": "Legacy Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Microphone", "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Speakers (Realtek)", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
    ]
    _patch_sounddevice(monkeypatch, hostapis, devices)

    idx, info = audio_capture._default_wasapi_output_device()

    assert idx == 2
    assert info["name"] == "Speakers (Realtek)"


def test_wasapi_output_device_falls_back_to_any_wasapi_speaker(monkeypatch):
    hostapis = [{"name": "Windows WASAPI", "default_output_device": -1}]
    devices = [
        {"name": "Mic", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Headphones", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
    ]
    _patch_sounddevice(monkeypatch, hostapis, devices)

    idx, info = audio_capture._default_wasapi_output_device()

    assert idx == 1
    assert info["name"] == "Headphones"


def test_default_soundcard_loopback_uses_default_speaker(monkeypatch):
    _speaker, loopback = _patch_soundcard(
        monkeypatch,
        speaker_name="Digital Output (High Definition Audio Device)",
        loopback_name="Digital Output (High Definition Audio Device)",
    )

    selected = audio_capture._default_soundcard_loopback()

    assert selected is loopback
    assert audio_capture.has_loopback() is True


def test_default_soundcard_loopback_fuzzy_fallback(monkeypatch):
    speaker = types.SimpleNamespace(name="Odyssey G95C (NVIDIA High Definition Audio)")
    exact_error = RuntimeError("exact lookup failed")
    candidate = types.SimpleNamespace(name="Loopback Odyssey G95C (NVIDIA High Definition Audio)")
    fake = types.SimpleNamespace(
        default_speaker=lambda: speaker,
        get_microphone=lambda name, include_loopback=False: (_ for _ in ()).throw(exact_error),
        all_microphones=lambda include_loopback=False: [candidate],
    )
    monkeypatch.setitem(sys.modules, "soundcard", fake)

    selected = audio_capture._default_soundcard_loopback()

    assert selected is candidate


def test_make_input_stream_rejects_loopback_because_soundcard_handles_system_audio(monkeypatch, tmp_path):
    _patch_sounddevice(monkeypatch, [], [])
    recorder = audio_capture.Recorder(
        audio_capture.RecorderConfig(output_path=tmp_path / "x.wav", capture_mic=False, capture_system=True)
    )

    with pytest.raises(audio_capture.AudioError, match="soundcard loopback"):
        recorder._make_input_stream(channels=1, blocksize=8000, wasapi_loopback=True)


def test_has_loopback_false_without_soundcard(monkeypatch):
    monkeypatch.setitem(sys.modules, "soundcard", None)

    assert audio_capture.has_loopback() is False


def test_mixes_mic_and_system_chunks_instead_of_appending_alternating_chunks(tmp_path):
    recorder = audio_capture.Recorder(
        audio_capture.RecorderConfig(output_path=tmp_path / "x.wav", capture_mic=True, capture_system=True)
    )
    mic = np.zeros((4, 1), dtype=np.float32)
    system = np.full((4, 1), 0.25, dtype=np.float32)
    buffers = {"mic": deque([mic]), "system": deque([system])}

    mixed = recorder._pop_mixed_chunk(buffers, require_all=True)

    assert mixed.shape == (4, 1)
    np.testing.assert_allclose(mixed, system)
    assert not buffers["mic"]
    assert not buffers["system"]


def test_mixing_clips_combined_sources_and_preserves_single_timeline(tmp_path):
    recorder = audio_capture.Recorder(
        audio_capture.RecorderConfig(output_path=tmp_path / "x.wav", capture_mic=True, capture_system=True)
    )

    mixed = recorder._mix_chunks([
        np.full((3, 1), 0.75, dtype=np.float32),
        np.full((3, 1), 0.75, dtype=np.float32),
    ])

    assert mixed.shape == (3, 1)
    np.testing.assert_allclose(mixed, np.ones((3, 1), dtype=np.float32))
