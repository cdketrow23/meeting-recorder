"""Microphone + system-audio capture with raw tracks and post-stop mixing.

Capture strategy
----------------
* Microphone: ``sounddevice`` default input stream.
* System audio: ``soundcard`` loopback microphone for the default speaker.

The Record button starts all enabled sources at once. Internally, each enabled
source is written to its own raw WAV plus timing metadata. When Stop is clicked,
the recorder pads each raw track by its first-chunk offset and mixes the tracks
into the final user-facing WAV. Keeping raw tracks makes sync/audio bugs much
easier to diagnose than live-mixing callbacks directly into the final file.
"""

from __future__ import annotations

import json
import math
import queue
import shutil
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
    raw_dir: Optional[Path] = None
    metadata_path: Optional[Path] = None
    keep_raw: bool = True

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
    """Stateful recorder that starts all enabled sources together.

    ``Recorder`` is not safe to share between threads except that ``is_active``,
    ``elapsed_seconds``, and ``level_max`` are documented as readable from any
    thread.
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

        self._q: "queue.Queue[tuple[str, np.ndarray, float]]" = queue.Queue()
        self._stop = threading.Event()
        self._active = threading.Event()
        self._started_at: Optional[float] = None
        self._frames_written: int = 0
        self._streams: list[sd.Stream] = []
        self._capture_threads: list[threading.Thread] = []
        self._thread: Optional[threading.Thread] = None
        self._level_max: float = 0.0
        self._raw_writers: dict[str, sf.SoundFile] = {}
        self._raw_paths: dict[str, Path] = {}
        self._source_meta: dict[str, dict] = {}
        self._metadata: dict = {}

    # -------------------- public API --------------------

    @property
    def is_active(self) -> bool:
        return self._active.is_set()

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    @property
    def level_max(self) -> float:
        return self._level_max

    def start(self) -> None:
        if self.is_active:
            raise AudioError("recorder already running")
        if not self.config.capture_mic and not self.config.capture_system:
            raise AudioError("at least one source must be enabled")

        self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_raw_outputs()
        self._frames_written = 0
        self._level_max = 0.0
        self._started_at = time.monotonic()
        self._metadata = {
            "session_start_monotonic": self._started_at,
            "sample_rate": self.config.sample_rate,
            "channels": self.config.channels,
            "final_wav": str(self.config.output_path),
            "sources": {},
        }
        self._stop.clear()
        self._active.set()
        self._thread = threading.Thread(target=self._run, name="meeting-recorder", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> Path:
        if not self.is_active:
            return self.config.output_path
        self._stop.set()
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
        self._close_raw_outputs()
        self._post_mix_raw_tracks()
        self._write_metadata()
        if not self.config.keep_raw:
            self._cleanup_raw_outputs()
        self._active.clear()
        return self.config.output_path

    # -------------------- raw track + mix helpers --------------------

    def _expected_sources(self) -> tuple[str, ...]:
        sources: list[str] = []
        if self.config.capture_mic:
            sources.append("mic")
        if self.config.capture_system:
            sources.append("system")
        return tuple(sources)

    def _raw_dir(self) -> Path:
        return Path(self.config.raw_dir) if self.config.raw_dir else self.config.output_path.parent / "raw" / self.config.output_path.stem

    def _metadata_path(self) -> Path:
        return Path(self.config.metadata_path) if self.config.metadata_path else self.config.output_path.with_name(f"{self.config.output_path.stem}_metadata.json")

    def _prepare_raw_outputs(self) -> None:
        raw_dir = self._raw_dir()
        raw_dir.mkdir(parents=True, exist_ok=True)
        self._raw_writers.clear()
        self._raw_paths.clear()
        self._source_meta.clear()
        for source in self._expected_sources():
            raw_path = raw_dir / f"{source}.wav"
            self._raw_paths[source] = raw_path
            self._source_meta[source] = {
                "path": str(raw_path),
                "sample_rate": self.config.sample_rate,
                "channels": self.config.channels,
                "first_chunk_monotonic": None,
                "first_chunk_offset_seconds": None,
                "frames": 0,
                "chunks": 0,
            }
            self._raw_writers[source] = sf.SoundFile(
                str(raw_path),
                mode="w",
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                subtype="PCM_16",
            )

    def _close_raw_outputs(self) -> None:
        for writer in self._raw_writers.values():
            try:
                writer.close()
            except Exception:
                pass
        self._raw_writers.clear()

    def _cleanup_raw_outputs(self) -> None:
        shutil.rmtree(self._raw_dir(), ignore_errors=True)

    def _write_metadata(self) -> None:
        if "session_start_monotonic" not in self._metadata:
            self._metadata["session_start_monotonic"] = self._started_at
            self._metadata["sample_rate"] = self.config.sample_rate
            self._metadata["channels"] = self.config.channels
            self._metadata["final_wav"] = str(self.config.output_path)
        stop_at = time.monotonic()
        self._metadata["session_stop_monotonic"] = stop_at
        self._metadata["duration_seconds"] = max(0.0, stop_at - (self._started_at or stop_at))
        self._metadata["sources"] = self._source_meta
        path = self._metadata_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._metadata, indent=2), encoding="utf-8")

    def _post_mix_raw_tracks(self) -> None:
        tracks: list[tuple[int, np.ndarray]] = []
        max_frames = 0
        for source, raw_path in self._raw_paths.items():
            if not raw_path.is_file() or self._source_meta.get(source, {}).get("frames", 0) <= 0:
                continue
            data, sr = sf.read(str(raw_path), dtype="float32", always_2d=True)
            if sr != self.config.sample_rate:
                raise AudioError(f"unexpected sample rate for {source}: {sr}")
            data = self._normalize_chunk(data)
            offset_s = float(self._source_meta[source].get("first_chunk_offset_seconds") or 0.0)
            offset_frames = max(0, int(round(offset_s * self.config.sample_rate)))
            tracks.append((offset_frames, data))
            max_frames = max(max_frames, offset_frames + data.shape[0])

        if not tracks:
            # Still create a valid empty-ish WAV instead of leaving no final file.
            sf.write(str(self.config.output_path), np.zeros((0, self.config.channels), dtype=np.float32), self.config.sample_rate, subtype="PCM_16")
            self._frames_written = 0
            return

        mixed = np.zeros((max_frames, self.config.channels), dtype=np.float32)
        for offset, data in tracks:
            mixed[offset : offset + data.shape[0], : data.shape[1]] += data
        mixed = np.clip(mixed, -1.0, 1.0)
        sf.write(str(self.config.output_path), mixed, self.config.sample_rate, subtype="PCM_16")
        self._frames_written = mixed.shape[0]

    # -------------------- internal capture loop --------------------

    def _run(self) -> None:
        try:
            self._open_streams()
            while (not self._stop.is_set() or not self._q.empty()) and self.elapsed_seconds < self.config.max_seconds:
                try:
                    source, chunk, captured_at = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk.size == 0:
                    continue
                self._write_raw_chunk(source, chunk, captured_at)
        except Exception as exc:
            self._safe_error(exc)

    def _write_raw_chunk(self, source: str, chunk: np.ndarray, captured_at: float) -> None:
        writer = self._raw_writers.get(source)
        if writer is None:
            return
        chunk = self._normalize_chunk(chunk)
        meta = self._source_meta[source]
        if meta["first_chunk_monotonic"] is None:
            meta["first_chunk_monotonic"] = captured_at
            meta["first_chunk_offset_seconds"] = max(0.0, captured_at - (self._started_at or captured_at))
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        if peak > self._level_max:
            self._level_max = peak
        writer.write(chunk)
        meta["frames"] += int(chunk.shape[0])
        meta["chunks"] += 1
        if self._on_amplitude:
            try:
                self._on_amplitude(self._level_max)
            except Exception as exc:
                self._safe_error(exc)

    def _normalize_chunk(self, chunk: np.ndarray) -> np.ndarray:
        chunk = np.asarray(chunk, dtype=np.float32)
        if self.config.channels == 1 and chunk.ndim == 2:
            chunk = chunk.mean(axis=1, keepdims=False)
        if chunk.ndim == 1:
            chunk = chunk.reshape(-1, 1)
        if chunk.shape[1] < self.config.channels:
            chunk = np.tile(chunk[:, :1], (1, self.config.channels))
        elif chunk.shape[1] != self.config.channels:
            chunk = chunk[:, : self.config.channels]
        return np.clip(chunk, -1.0, 1.0)

    def _mix_chunks(self, chunks: Iterable[np.ndarray]) -> np.ndarray:
        normalized = [self._normalize_chunk(chunk) for chunk in chunks if chunk.size]
        if not normalized:
            return np.zeros((0, self.config.channels), dtype=np.float32)
        max_frames = max(chunk.shape[0] for chunk in normalized)
        mixed = np.zeros((max_frames, self.config.channels), dtype=np.float32)
        for chunk in normalized:
            mixed[: chunk.shape[0], : chunk.shape[1]] += chunk
        return np.clip(mixed, -1.0, 1.0)

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
                if self.config.capture_mic:
                    self._safe_warning(f"system audio capture unavailable ({exc}); mic-only")
                else:
                    raise AudioError("system audio capture unavailable and mic is off") from exc

    def _make_input_stream(self, channels: int, blocksize: int, wasapi_loopback: bool) -> sd.Stream:
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
        try:
            loopback = _default_soundcard_loopback()
            with loopback.recorder(samplerate=self.config.sample_rate, channels=self.config.channels) as rec:
                while not self._stop.is_set():
                    captured_at = time.monotonic()
                    chunk = rec.record(numframes=blocksize)
                    self._q.put(("system", np.asarray(chunk, dtype=np.float32).copy(), captured_at))
        except Exception as exc:
            self._safe_error(AudioError(f"system audio capture unavailable ({exc})"))

    def _make_callback(self, source: str):
        def _cb(indata, _frames, _time_info, _status):
            if _status:
                self._safe_warning(f"[{source}] {_status}")
            captured_at = time.monotonic()
            self._q.put((source, np.asarray(indata, dtype=np.float32).copy(), captured_at))
        return _cb

    def _safe_warning(self, msg: str) -> None:
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
    """Return ``(device_index, device_info)`` for the default WASAPI speaker."""
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
    """Return SoundCard's loopback microphone for the current default speaker."""
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
