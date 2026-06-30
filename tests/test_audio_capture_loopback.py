from __future__ import annotations

import types

import pytest

from meetingrecorder import audio_capture


class _FakeWasapiSettings:
    def __init__(self, loopback: bool = False):
        self.loopback = loopback


def _patch_sounddevice(monkeypatch, hostapis, devices):
    created = []

    def fake_input_stream(**kwargs):
        created.append(kwargs)
        return types.SimpleNamespace(start=lambda: None, stop=lambda: None, close=lambda: None)

    monkeypatch.setattr(audio_capture.sd, "query_hostapis", lambda: hostapis)
    monkeypatch.setattr(audio_capture.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(audio_capture.sd, "WasapiSettings", _FakeWasapiSettings)
    monkeypatch.setattr(audio_capture.sd, "InputStream", fake_input_stream)
    return created


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
    assert audio_capture.has_loopback() is True


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


def test_system_loopback_stream_uses_output_device_and_loopback(monkeypatch, tmp_path):
    hostapis = [{"name": "Windows WASAPI", "default_output_device": 1}]
    devices = [
        {"name": "Mic", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
    ]
    created = _patch_sounddevice(monkeypatch, hostapis, devices)
    recorder = audio_capture.Recorder(
        audio_capture.RecorderConfig(output_path=tmp_path / "x.wav", capture_mic=False, capture_system=True)
    )

    recorder._make_input_stream(channels=1, blocksize=8000, wasapi_loopback=True)

    assert created, "InputStream should have been created"
    kwargs = created[0]
    assert kwargs["device"] == 1
    assert kwargs["channels"] == 1
    assert isinstance(kwargs["extra_settings"], _FakeWasapiSettings)
    assert kwargs["extra_settings"].loopback is True


def test_has_loopback_false_without_wasapi(monkeypatch):
    _patch_sounddevice(
        monkeypatch,
        [{"name": "MME", "default_output_device": 0}],
        [{"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2}],
    )

    assert audio_capture.has_loopback() is False
