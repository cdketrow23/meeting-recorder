"""Transcription backends with a uniform :class:`Transcriber` interface.

Default backend is Vosk — small, MIT, completely offline. Optional faster-whisper
is auto-detected if installed. If no backend is usable we return an empty
:class:`Transcript` rather than failing — the WAV file is still produced.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

from .transcript_format import Segment, Transcript

_log = logging.getLogger(__name__)

VOSK_SMALL_EN_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_SMALL_EN_DIRNAME = "vosk-model-small-en-us-0.15"


def _app_data_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "MeetingRecorder"
    return Path.home() / ".cache" / "meetingrecorder"


def _cached_vosk_model_dir() -> Path:
    return _app_data_dir() / "models" / VOSK_SMALL_EN_DIRNAME


def ensure_vosk_model(model_path: Optional[str] = None) -> Path:
    """Return a usable Vosk model directory, downloading the small English model if needed.

    This makes transcription work out-of-the-box for normal users instead of
    silently creating only a WAV file when the model has not been manually
    installed yet.
    """
    explicit = model_path or os.environ.get("VOSK_MODEL")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            return p
        raise RuntimeError(f"configured VOSK_MODEL does not exist or is not a directory: {p}")

    candidates = [
        Path.cwd() / VOSK_SMALL_EN_DIRNAME,
        Path.cwd() / "vosk-model",
        Path.home() / "Documents" / "MeetingRecorder" / "vosk-model",
        _cached_vosk_model_dir(),
    ]
    for c in candidates:
        if c.is_dir():
            return c

    target = _cached_vosk_model_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    zip_path = target.parent / f"{VOSK_SMALL_EN_DIRNAME}.zip"
    tmp_dir = target.parent / f".{VOSK_SMALL_EN_DIRNAME}.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        _log.info("Downloading Vosk model to %s", zip_path)
        urllib.request.urlretrieve(VOSK_SMALL_EN_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir)
        extracted = tmp_dir / VOSK_SMALL_EN_DIRNAME
        if not extracted.is_dir():
            dirs = [p for p in tmp_dir.iterdir() if p.is_dir()]
            if len(dirs) == 1:
                extracted = dirs[0]
        if not extracted.is_dir():
            raise RuntimeError("downloaded Vosk zip did not contain a model directory")
        shutil.move(str(extracted), str(target))
        return target
    finally:
        try:
            zip_path.unlink()
        except FileNotFoundError:
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)


class Transcriber(Protocol):
    """Push bytes in, pull :class:`Transcript` out."""

    def feed(self, audio_chunk) -> None: ...
    def finish(self) -> Transcript: ...


# -------------------- Vosk backend --------------------

@dataclass
class VoskTranscriber:
    """Streaming Vosk recognizer. Buffer-flushed every ``accept_buffer`` chunks."""

    sample_rate: int
    model_path: Optional[str] = None
    language: str = "en-us"
    accept_buffer: int = 8

    def __post_init__(self) -> None:
        # Imported lazily so the rest of the app doesn't need Vosk installed
        from vosk import KaldiRecognizer, Model

        path = ensure_vosk_model(self.model_path)
        self._Model = Model
        self._KaldiRecognizer = KaldiRecognizer
        self._model = Model(str(path))
        self._recognizer = KaldiRecognizer(self._model, self.sample_rate)
        self._recognizer.SetWords(True)
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._segments: list[Segment] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def feed(self, audio_chunk) -> None:
        if audio_chunk is None:
            return
        if hasattr(audio_chunk, "ndim"):
            data = _float_audio_to_pcm16_bytes(audio_chunk)
        else:
            data = bytes(audio_chunk)
        self._q.put(data)

    def finish(self) -> Transcript:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=20)
        # Flush any final partial
        if self._recognizer.AcceptWaveform(b"\x00\x00"):
            pass
        end_result = json.loads(self._recognizer.FinalResult() or "{}")
        self._consume(end_result)
        return Transcript(segments=self._segments, engine="vosk", language=self.language)

    # -------------------- internals --------------------

    def _run(self) -> None:
        buffer_count = 0
        while not self._stop.is_set() or not self._q.empty():
            try:
                data = self._q.get(timeout=0.2)
            except queue.Empty:
                buffer_count += 1
                if buffer_count >= self.accept_buffer and self._recognizer:
                    partial = json.loads(self._recognizer.PartialResult() or "{}")
                    self._consume(partial, partial=True)
                    buffer_count = 0
                continue
            buffer_count += 1
            if self._recognizer.AcceptWaveform(data):
                result = json.loads(self._recognizer.Result() or "{}")
                self._consume(result)

    def _consume(self, payload: dict, partial: bool = False) -> None:
        # Vosk's "result" key holds finalized segments; "partial" holds in-progress
        text_key = "partial" if partial else "result"
        segs = payload.get(text_key) or []
        if isinstance(segs, str):
            segs = [{"word": segs, "conf": 1.0, "start": 0.0, "end": 0.0}]
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            text = (seg.get("word") or seg.get("text") or "").strip()
            if not text:
                continue
            start = float(seg.get("start", 0.0) or 0.0)
            end = float(seg.get("end", start + 1.0) or (start + 1.0))
            conf = seg.get("conf")
            try:
                conf_f = float(conf) if conf is not None else None
            except (TypeError, ValueError):
                conf_f = None
            self._segments.append(Segment(
                start=start,
                end=end,
                text=text,
                confidence=conf_f,
            ))


# -------------------- faster-whisper backend (optional) --------------------

@dataclass
class WhisperTranscriber:
    sample_rate: int
    model_size: str = "small.en"
    language: str = "en"
    device: str = "cpu"
    compute_type: str = "int8"

    def __post_init__(self) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional
            raise RuntimeError("faster-whisper is not installed") from exc
        self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)

    def feed(self, audio_chunk) -> None:  # pragma: no cover - optional path
        return None  # whisper path is offline-only here

    def finish(self) -> Transcript:
        # Whisper consumes the full file at once; we trigger that via the GUI's
        # post-stop code path (transcribe_file below).
        return Transcript(segments=[], engine="whisper", language=self.language)


# -------------------- Convenience entry point --------------------

def transcribe_file(
    wav_path: Path,
    backend: str = "auto",
    language: str = "en",
) -> Transcript:
    """Transcribe a WAV file. ``backend='auto'`` picks the best available."""
    wav_path = Path(wav_path)
    if not wav_path.is_file():
        raise FileNotFoundError(wav_path)

    if backend == "vosk":
        return _transcribe_with_vosk(wav_path, language=language)
    if backend == "whisper":
        return _transcribe_with_whisper(wav_path, language=language)

    # auto
    if _vosk_available():
        try:
            return _transcribe_with_vosk(wav_path, language=language)
        except Exception as exc:
            _log.warning("Vosk failed (%s); trying whisper", exc)
    if _whisper_available():
        try:
            return _transcribe_with_whisper(wav_path, language=language)
        except Exception as exc:
            _log.warning("Whisper failed (%s)", exc)
    return Transcript(segments=[], engine="none", language=language)


def _float_audio_to_pcm16_bytes(audio_chunk) -> bytes:
    """Convert soundfile float audio arrays to mono 16-bit PCM bytes for Vosk."""
    arr = np.asarray(audio_chunk, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def _read_wav_chunks(wav_path: Path, chunk_seconds: float = 0.5):
    import soundfile as sf

    with sf.SoundFile(str(wav_path)) as f:
        sr = f.samplerate
        block = int(chunk_seconds * sr)
        while True:
            data = f.read(block, dtype="float32", always_2d=False)
            if data.size == 0:
                return
            yield data, sr


def _transcribe_with_vosk(wav_path: Path, language: str) -> Transcript:
    rec: Optional[VoskTranscriber] = None
    for data, sr in _read_wav_chunks(wav_path):
        if rec is None:
            rec = VoskTranscriber(sample_rate=sr, language=language)
        rec.feed(data)
    return rec.finish() if rec else Transcript(engine="vosk", language=language)


def _transcribe_with_whisper(wav_path: Path, language: str) -> Transcript:
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    segments_iter, _info = model.transcribe(str(wav_path), language=language)
    segments = [
        Segment(start=float(s.start), end=float(s.end), text=str(s.text).strip(), confidence=float(getattr(s, "avg_logprob", 0.0)) or None)
        for s in segments_iter
    ]
    return Transcript(segments=segments, engine="whisper", language=language)


def _vosk_available() -> bool:
    try:
        import vosk  # noqa: F401

        return True
    except ImportError:
        return False


def _whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False
