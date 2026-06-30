"""Microphone + system-audio capture to a single normalized WAV file.

Capture strategy
----------------
* Microphone: ``sounddevice`` default input stream.
* System audio: WASAPI loopback via ``sounddevice``. On Windows 10/11 the
  default output device exposes a loopback input when ``sd.WasapiSettings``
  is used. On other platforms the system-audio track is reported as
  unavailable and the recorder happily degrades to mic-only.

This module is intentionally Windows-friendly but is import-safe on any
platform. ``Recorder.start`` will raise :class:`AudioError` with a clear
message if the platform does not support loopback.

The implementation writes a 16-bit PCM mono WAV at the chosen sample rate
using the ``soundfile`` library, with chunked append so the file is always
valid even after a forced stop.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE_DEFAULT = 16_000  # 16 kHz mono is fine for speech and tiny on disk.


class AudioError(RuntimeError):
    """Raised when audio capture cannot start or fails mid-session."""


@dataclass
class RecorderConfig:
    sample_rate: int = SAMPLE_RATE_DEFAULT
    channels: int = 1  # mono is plenty for speech + a fraction of the disk space
    capture_mic: bool = True
    capture_system: bool = True
    output_path: Path = Path("recording.wav")
    chunk_seconds: float = 0.5
    max_seconds: int = 4 * 60 * 60  # 4 hour hard cap

    def __post_init__(self) -> None:
        if self.sample_rate < 8_000 or self.sample_rate > 48_000:
            raise AudioError(f"unsupported sample rate: {self.sample_rate}")
        if self.channels not in (1, 2):
            raise AudioError(f"unsupported channel count: {self.channels}")
        if self.chunk_seconds <= 0:
            raise AudioError("chunk_seconds must be > 0")
        if self.max_seconds <= 0:
            raise AudioError("max_seconds must be > 0")


class Recorder:
    """Stateful recorder that writes a single WAV across the whole session.

    Typical GUI flow::

        rec = Recorder(RecorderConfig(output_path=...))
        rec.start()           # spawns capture thread, opens WAV
        ...
        rec.stop()            # blocks until all chunks flushed + file closed

    ``Recorder`` is **not** safe to share between threads except that
    ``is_active`` is documented as readable from any thread.
    """

    def __init__(
        self,
        config: RecorderConfig,
        on_amplitude: Optional[Callable[[float], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.config = config
        self._on_amplitude = on_amplitude
        self._on_error = on_error

        self._q: "queue.Queue[tuple[str, np.ndarray]]" = queue.Queue()
        self._stop = threading.Event()
        self._active = threading.Event()
        self._started_at: Optional[float] = None
        self._frames_written: int = 0
        self._writer: Optional[sf.SoundFile] = None
        self._streams: list[sd.Stream] = []
        self._capture_threads: list[threading.Thread] = []
        self._thread: Optional[threading.Thread] = None
        self._level_max: float = 0.0

    # -------------------- public API --------------------

    @property
    def is_active(self) -> bool:
        """``True`` while capture is in progress (any thread)."""
        return self._active.is_set()

    @property
    def elapsed_seconds(self) -> float:
        """Seconds since :meth:`start` was called, regardless of stop."""
        if self._started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    @property
    def level_max(self) -> float:
        """Peak absolute sample value observed so far (0.0 - 1.0)."""
        return self._level_max

    def start(self) -> None:
        if self.is_active:
            raise AudioError("recorder already running")
        if not self.config.capture_mic and not self.config.capture_system:
            raise AudioError("at least one source must be enabled")

        self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = sf.SoundFile(
            str(self.config.output_path),
            mode="w",
            samplerate=self.config.sample_rate,
            channels=self.config.channels,
            subtype="PCM_16",
        )
        self._frames_written = 0
        self._level_max = 0.0
        self._started_at = time.monotonic()
        self._stop.clear()
        self._active.set()
        self._thread = threading.Thread(target=self._run, name="meeting-recorder", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> Path:
        if not self.is_active:
            return self.config.output_path
        self._stop.set()
        # Closing streams flushes any pending chunks through the queue
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        for t in self._capture_threads:
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self._safe_error(AudioError("capture thread did not stop cleanly"))
        self._streams.clear()
        if self._writer is not None:
            try:
                self._writer.close()
            finally:
                self._writer = None
        self._active.clear()
        return self.config.output_path

    # -------------------- internal capture loop --------------------

    def _run(self) -> None:
        try:
            self._open_streams()
            while not self._stop.is_set() and self.elapsed_seconds < self.config.max_seconds:
                try:
                    source, chunk = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk.size == 0:
                    continue
                if self.config.channels == 1 and chunk.ndim == 2:
                    chunk = chunk.mean(axis=1, keepdims=False)
                if chunk.ndim == 1:
                    chunk = chunk.reshape(-1, 1)
                if chunk.shape[1] != self.config.channels:
                    # last-ditch: take the first channel
                    chunk = chunk[:, : self.config.channels]
                peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
                if peak > self._level_max:
                    self._level_max = peak
                if self._writer is not None:
                    self._writer.write(chunk)
                    self._frames_written += chunk.shape[0]
                if self._on_amplitude:
                    try:
                        self._on_amplitude(self._level_max)
                    except Exception as exc:
                        self._safe_error(exc)
        except Exception as exc:
            self._safe_error(exc)

    def _open_streams(self) -> None:
        blocksize = max(1, int(self.config.chunk_seconds * self.config.sample_rate))

        if self.config.capture_mic:
            try:
                mic = self._make_input_stream(
                    channels=self.config.channels,
                    blocksize=blocksize,
                    wasapi_loopback=False,
                )
                mic.start()
                self._streams.append(mic)
            except Exception as exc:
                raise AudioError(f"could not open microphone: {exc}") from exc

        if self.config.capture_system:
            try:
                sys_thread = threading.Thread(
                    target=self._soundcard_loopback_loop,
                    args=(blocksize,),
                    name="meeting-recorder-system-loopback",
                    daemon=True,
                )
                sys_thread.start()
                self._capture_threads.append(sys_thread)
            except Exception as exc:
                # Degrade gracefully if loopback isn't available
                if self.config.capture_mic:
                    self._safe_warning(f"system audio capture unavailable ({exc}); mic-only")
                else:
                    raise AudioError(
                        "system audio capture unavailable and mic is off"
                    ) from exc

    def _make_input_stream(self, channels: int, blocksize: int, wasapi_loopback: bool) -> sd.Stream:
        """Create the microphone input stream.

        System/speaker output is captured separately through the ``soundcard``
        package because Python sounddevice's Windows wheels do not expose a
        stable WASAPI loopback setting.
        """
        if wasapi_loopback:
            raise AudioError("internal error: system audio should use soundcard loopback")
        kwargs = dict(
            samplerate=self.config.sample_rate,
            blocksize=blocksize,
            dtype="float32",
            callback=self._make_callback("mic"),
            channels=channels,
        )
        return sd.InputStream(**kwargs)

    def _soundcard_loopback_loop(self, blocksize: int) -> None:
        """Capture speaker/output audio via SoundCard's WASAPI loopback mic."""
        try:
            loopback = _default_soundcard_loopback()
            # SoundCard's recorder returns float32 frames in [-1, 1]. Keep this
            # loop chunked so Stop can interrupt within roughly one block.
            with loopback.recorder(samplerate=self.config.sample_rate, channels=self.config.channels) as rec:
                while not self._stop.is_set():
                    chunk = rec.record(numframes=blocksize)
                    self._q.put(("system", np.asarray(chunk, dtype=np.float32).copy()))
        except Exception as exc:
            self._safe_error(AudioError(f"system audio capture unavailable ({exc})"))

    def _make_callback(self, source: str):
        def _cb(indata, _frames, _time_info, _status):
            if _status:
                self._safe_warning(f"[{source}] {_status}")
            self._q.put((source, np.asarray(indata, dtype=np.float32).copy()))
        return _cb

    def _safe_warning(self, msg: str) -> None:
        # Soft warnings are not fatal; route them through on_error if it exists
        if self._on_error:
            try:
                self._on_error(AudioError(msg))
            except Exception:
                pass

    def _safe_error(self, exc: Exception) -> None:
        if self._on_error:
            try:
                self._on_error(exc)
            except Exception:
                pass


def _hostapis() -> list[dict]:
    """Return sounddevice host APIs as a list, normalizing old/new versions."""
    apis = sd.query_hostapis()
    if isinstance(apis, dict):
        return [apis]
    return list(apis)


def _devices() -> list[dict]:
    """Return sounddevice devices as a list, with an ``index`` key filled in."""
    devices = sd.query_devices()
    if isinstance(devices, dict):
        devices = [devices]
    out: list[dict] = []
    for idx, d in enumerate(devices):
        dd = dict(d)
        dd.setdefault("index", idx)
        out.append(dd)
    return out


def _wasapi_hostapi_index() -> int | None:
    """Return the first WASAPI host API index, or ``None`` if unavailable."""
    for idx, api in enumerate(_hostapis()):
        if "wasapi" in str(api.get("name", "")).lower():
            return idx
    return None


def _default_wasapi_output_device() -> tuple[int, dict]:
    """Return ``(device_index, device_info)`` for the default WASAPI speaker.

    Windows speaker capture is implemented by opening the *output* device with
    WASAPI loopback enabled. Prefer the WASAPI host API's own default output;
    fall back to any output device on the WASAPI host API.
    """
    api_index = _wasapi_hostapi_index()
    if api_index is None:
        raise AudioError("WASAPI host API is not available on this machine")

    apis = _hostapis()
    api = apis[api_index]
    devices = _devices()
    default_idx = api.get("default_output_device")
    if isinstance(default_idx, int) and 0 <= default_idx < len(devices):
        info = devices[default_idx]
        if int(info.get("max_output_channels", 0) or 0) > 0:
            return int(info.get("index", default_idx)), info

    for idx, info in enumerate(devices):
        if info.get("hostapi") == api_index and int(info.get("max_output_channels", 0) or 0) > 0:
            return int(info.get("index", idx)), info

    raise AudioError("no WASAPI output/speaker device found for loopback capture")


def _default_soundcard_loopback():
    """Return SoundCard's loopback microphone for the current default speaker.

    ``soundcard`` exposes Windows speaker/output capture as a loopback
    microphone. Matching by the default speaker name follows the user's active
    output route (Digital Output, monitor audio, headset, etc.).
    """
    try:
        import soundcard as sc
    except ImportError as exc:
        raise AudioError("soundcard package is required for speaker/output capture") from exc

    speaker = sc.default_speaker()
    if speaker is None:
        raise AudioError("no default speaker/output device found")
    try:
        return sc.get_microphone(speaker.name, include_loopback=True)
    except Exception as exc:
        # Fall back to a fuzzy contains match for driver names that differ
        # slightly between speaker and loopback endpoint labels.
        speaker_name = str(getattr(speaker, "name", "")).lower()
        for mic in sc.all_microphones(include_loopback=True):
            mic_name = str(getattr(mic, "name", "")).lower()
            if speaker_name and (speaker_name in mic_name or mic_name in speaker_name):
                return mic
        raise AudioError(f"no loopback device found for default speaker {speaker!r}") from exc


def list_input_devices() -> list[dict]:
    """Return a snapshot of available input devices for the settings dialog."""
    out: list[dict] = []
    for idx, d in enumerate(_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append({
                "index": int(d.get("index", idx)),
                "name": d.get("name"),
                "host_api": d.get("hostapi"),
                "channels": d.get("max_input_channels"),
            })
    return out


def has_loopback() -> bool:
    """Best-effort check: can Windows speaker/output audio be captured?"""
    try:
        _default_soundcard_loopback()
        return True
    except Exception:
        return False
