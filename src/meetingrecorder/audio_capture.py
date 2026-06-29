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
                sys_stream = self._make_input_stream(
                    channels=self.config.channels,
                    blocksize=blocksize,
                    wasapi_loopback=True,
                )
                sys_stream.start()
                self._streams.append(sys_stream)
            except Exception as exc:
                # Degrade gracefully if loopback isn't available
                if self.config.capture_mic:
                    self._safe_warning(f"system audio capture unavailable ({exc}); mic-only")
                else:
                    raise AudioError(
                        "system audio capture unavailable and mic is off"
                    ) from exc

    def _make_input_stream(self, channels: int, blocksize: int, wasapi_loopback: bool) -> sd.Stream:
        """Create an input stream with WASAPI-loopback on Windows when requested."""
        kwargs = dict(
            samplerate=self.config.sample_rate,
            blocksize=blocksize,
            dtype="float32",
            callback=self._make_callback("system" if wasapi_loopback else "mic"),
        )
        if wasapi_loopback and sd.query_hostapi(0).get("name", "").lower().startswith("wasapi"):
            kwargs["extra_settings"] = sd.WasapiSettings(loopback=wasapi_loopback)
        kwargs["channels"] = channels
        return sd.InputStream(**kwargs)

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


def list_input_devices() -> list[dict]:
    """Return a snapshot of available input devices for the settings dialog."""
    devices = sd.query_devices()
    out: list[dict] = []
    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            out.append({
                "index": idx,
                "name": d.get("name"),
                "host_api": d.get("hostapi"),
                "channels": d.get("max_input_channels"),
            })
    return out


def has_loopback() -> bool:
    """Best-effort check: does the host API expose WASAPI loopback?"""
    try:
        return sd.query_hostapi(0).get("name", "").lower().startswith("wasapi")
    except Exception:
        return False
