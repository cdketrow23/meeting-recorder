"""Tkinter GUI for MeetingRecorder.

Design notes:

- Tk is bundled with the official CPython Windows installer, so it ships
  with the executable without any extra dependency.
- All audio capture runs on a worker thread owned by :class:`Recorder`. The
  GUI thread polls a small Tk ``after`` loop every 100 ms for level,
  elapsed time, and state updates.
- The window title and a colored badge make the recording state obvious
  to anyone watching the screen — by design.
- The app always asks the user to pick an output folder on first launch;
  later sessions remember the last choice in a tiny config file.
"""

from __future__ import annotations

import json
import logging
import threading
import tkinter as tk
import traceback
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import __version__
from .audio_capture import (
    AudioError,
    Recorder,
    RecorderConfig,
    has_loopback,
    list_input_devices,
)
from .io_utils import default_output_dir, session_basename, session_paths
from .transcribe import transcribe_file
from .transcript_format import write_transcript

_log = logging.getLogger(__name__)

APP_NAME = "MeetingRecorder"

# -------------------- config persistence --------------------

_CFG_PATH = Path.home() / ".meetingrecorder.json"
_DEFAULT_CFG = {
    "output_dir": None,
    "sample_rate": 16000,
    "channels": 1,
    "capture_mic": True,
    "capture_system": True,
    "transcription_backend": "auto",
    "language": "en",
    "version": __version__,
}


def _load_config() -> dict:
    if not _CFG_PATH.is_file():
        return dict(_DEFAULT_CFG)
    try:
        cfg = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT_CFG)
    # Schema-tolerant load
    out = dict(_DEFAULT_CFG)
    out.update({k: v for k, v in cfg.items() if k in _DEFAULT_CFG})
    return out


def _save_config(cfg: dict) -> None:
    _CFG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# -------------------- main GUI --------------------

class App:
    """Tkinter root + helpers. ``App.run()`` blocks until the window closes."""

    def __init__(self) -> None:
        self.cfg = _load_config()
        # Guarantee a usable default output dir on first launch
        if not self.cfg.get("output_dir"):
            self.cfg["output_dir"] = str(default_output_dir())
            _save_config(self.cfg)

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{__version__}")
        self.root.geometry("560x420")
        self.root.minsize(520, 400)

        self._build_vars()
        self._build_ui()

        self._recorder: Optional[Recorder] = None
        self._record_thread: Optional[threading.Thread] = None
        self._current_paths: Optional[dict[str, Path]] = None
        self._finalizing = False

        # periodic UI refresh
        self.root.after(100, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # surface audio errors nicely
        self._last_error: Optional[str] = None

    # -------------------- UI --------------------

    def _build_vars(self) -> None:
        self.var_status = tk.StringVar(value="Idle")
        self.var_output = tk.StringVar(value=self.cfg.get("output_dir") or "")
        self.var_elapsed = tk.StringVar(value="00:00:00")
        self.var_level = tk.DoubleVar(value=0.0)
        self.var_capture_mic = tk.BooleanVar(value=bool(self.cfg.get("capture_mic", True)))
        self.var_capture_system = tk.BooleanVar(
            value=bool(self.cfg.get("capture_system", True))
            and self.cfg.get("system_available", True)
        )
        self.var_backend = tk.StringVar(value=self.cfg.get("transcription_backend", "auto"))
        self.var_language = tk.StringVar(value=self.cfg.get("language", "en"))
        self.var_device = tk.StringVar(value="Default")
        self.var_record_btn = tk.StringVar(value="Record")

    def _build_ui(self) -> None:
        style = ttk.Style()
        # Force a clearly visible "recording" style on Windows default theme
        try:
            style.theme_use("vista")
        except tk.TclError:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
        style.configure("Record.TLabel", foreground="white", background="#c0182c", padding=6)
        style.configure("Idle.TLabel", foreground="white", background="#1f6feb", padding=6)

        root = self.root
        pad = {"padx": 8, "pady": 4}

        # Status bar at the top
        header = ttk.Frame(root)
        header.pack(fill="x", **pad)
        self.lbl_status = ttk.Label(
            header,
            textvariable=self.var_status,
            style="Idle.TLabel",
            anchor="center",
        )
        self.lbl_status.pack(fill="x")

        # Output folder row
        out_frame = ttk.LabelFrame(root, text="Output folder")
        out_frame.pack(fill="x", **pad)
        ttk.Entry(out_frame, textvariable=self.var_output).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=6
        )
        ttk.Button(out_frame, text="Choose...", command=self._choose_output).pack(
            side="left", padx=(0, 8), pady=6
        )

        # Sources frame
        src = ttk.LabelFrame(root, text="Sources")
        src.pack(fill="x", **pad)
        ttk.Checkbutton(
            src,
            text="Microphone",
            variable=self.var_capture_mic,
        ).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        sys_btn = ttk.Checkbutton(
            src,
            text="System audio (WASAPI loopback)",
            variable=self.var_capture_system,
        )
        sys_btn.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        if not has_loopback():
            sys_btn.state(["disabled"])
            self.var_capture_system.set(False)
            self.var_status.set("Loopback unavailable — mic only")

        ttk.Label(src, text="Input device").grid(row=1, column=0, sticky="w", padx=8)
        devices = list_input_devices() or [{"index": None, "name": "Default"}]
        device_names = ["Default"] + [d["name"] for d in devices if d.get("name")]
        self._device_map = {"Default": None}
        for d in devices:
            self._device_map[d["name"]] = d["index"]
        ttk.Combobox(
            src,
            textvariable=self.var_device,
            values=device_names,
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        src.columnconfigure(1, weight=1)

        # Settings frame
        opt = ttk.LabelFrame(root, text="Transcription")
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="Backend").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Combobox(
            opt,
            textvariable=self.var_backend,
            values=("auto", "vosk", "whisper"),
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        ttk.Label(opt, text="Language").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Combobox(
            opt,
            textvariable=self.var_language,
            values=("en", "en-us", "en-gb", "es", "fr", "de", "ja", "zh"),
            state="normal",
        ).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        opt.columnconfigure(1, weight=1)

        # Status frame (elapsed + level)
        live = ttk.LabelFrame(root, text="Recording live")
        live.pack(fill="x", **pad)
        ttk.Label(live, text="Elapsed").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Label(live, textvariable=self.var_elapsed, font=("Consolas", 12)).grid(
            row=0, column=1, sticky="w", padx=8
        )
        ttk.Label(live, text="Level").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Progressbar(
            live,
            orient="horizontal",
            maximum=1.0,
            mode="determinate",
            variable=self.var_level,
            length=300,
        ).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        live.columnconfigure(1, weight=1)

        # Buttons
        btn_row = ttk.Frame(root)
        btn_row.pack(fill="x", **pad)
        self.btn_record = ttk.Button(
            btn_row, textvariable=self.var_record_btn, command=self._toggle_recording, width=16
        )
        self.btn_record.pack(side="left", padx=8, pady=6)
        ttk.Button(btn_row, text="Open output folder", command=self._open_output).pack(
            side="left", padx=8
        )
        ttk.Button(btn_row, text="About", command=self._about).pack(side="right", padx=8)

        # Footer
        foot = ttk.Label(
            root,
            text="Records audio on this computer. You are responsible for any legal use.",
            foreground="#555",
            wraplength=540,
            justify="center",
        )
        foot.pack(fill="x", padx=8, pady=(2, 8))

    # -------------------- actions --------------------

    def _choose_output(self) -> None:
        if self._recorder is not None and self._recorder.is_active:
            messagebox.showinfo(APP_NAME, "Stop recording first.")
            return
        chosen = filedialog.askdirectory(initialdir=self.var_output.get() or None, title="Choose output folder")
        if chosen:
            self.var_output.set(chosen)
            self.cfg["output_dir"] = chosen
            _save_config(self.cfg)

    def _open_output(self) -> None:
        import os
        import subprocess

        path = self.var_output.get()
        if not path:
            return
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not open folder: {exc}")

    def _about(self) -> None:
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} v{__version__}\n\n"
            "Records microphone and system audio to a WAV file, then produces a "
            "local transcript next to it. Nothing leaves this computer by default.\n\n"
            "You are responsible for complying with all applicable recording "
            "consent and privacy laws in your jurisdiction.",
        )

    def _toggle_recording(self) -> None:
        if self._recorder is None:
            self._start_recording()
        else:
            self._stop_recording()

    # -------------------- recording lifecycle --------------------

    def _start_recording(self) -> None:
        out_dir = Path(self.var_output.get() or default_output_dir())
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not create output folder:\n{exc}")
            return

        # Persist settings for next launch
        self.cfg.update({
            "output_dir": str(out_dir),
            "capture_mic": bool(self.var_capture_mic.get()),
            "capture_system": bool(self.var_capture_system.get()) and has_loopback(),
            "transcription_backend": self.var_backend.get(),
            "language": self.var_language.get(),
            "system_available": has_loopback(),
        })
        _save_config(self.cfg)

        capture_mic = bool(self.var_capture_mic.get())
        capture_system = bool(self.var_capture_system.get())
        if not capture_mic and not capture_system:
            messagebox.showerror(APP_NAME, "Enable at least one source.")
            return

        basename = session_basename(prefix="meeting")
        paths = session_paths(out_dir, basename)
        self._current_paths = paths

        def _on_amp(level: float) -> None:
            self.var_level.set(min(1.0, max(0.0, level)))

        def _on_error(exc: Exception) -> None:
            self._last_error = str(exc)
            # Wake up the Tk loop to surface to user
            self.root.after(0, lambda: self._surface_error(exc))

        try:
            rec = Recorder(
                RecorderConfig(
                    sample_rate=int(self.cfg.get("sample_rate") or 16000),
                    channels=int(self.cfg.get("channels") or 1),
                    capture_mic=capture_mic,
                    capture_system=capture_system,
                    output_path=paths["wav"],
                ),
                on_amplitude=_on_amp,
                on_error=_on_error,
            )
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Recorder init failed:\n{exc}")
            return

        self._recorder = rec
        try:
            rec.start()
        except AudioError as exc:
            messagebox.showerror(APP_NAME, f"Audio start failed:\n{exc}")
            self._recorder = None
            return
        self._after_start_state()

    def _after_start_state(self) -> None:
        self.var_status.set("●  Recording — visible indicator is intentional")
        self.lbl_status.configure(style="Record.TLabel")
        self.root.title(f"{APP_NAME} — ● Recording")
        self.var_record_btn.set("Stop")
        self.btn_record.state(["disabled"])  # re-enable on next tick
        self.root.after(120, lambda: self.btn_record.state(["!disabled"]))

    def _stop_recording(self) -> None:
        if self._recorder is None:
            return
        if self._finalizing:
            return
        self._finalizing = True
        self.var_status.set("Stopping, finalizing audio...")
        self.root.update_idletasks()

        def _do_stop() -> None:
            try:
                wav_path = self._recorder.stop() if self._recorder is not None else None
            except Exception as exc:
                self.root.after(0, lambda: self._surface_error(exc))
                self._recorder = None
                self._finalizing = False
                return
            # Transcribe after the recorder is fully stopped
            if wav_path is not None and self._current_paths is not None:
                self.var_status.set("Transcribing...")
                self.root.update_idletasks()
                try:
                    transcript = transcribe_file(
                        wav_path,
                        backend=self.var_backend.get(),
                        language=self.var_language.get(),
                    )
                    write_transcript(transcript, self._current_paths)
                except Exception as exc:
                    _log.warning("Transcription failed: %s", exc)
                    self.root.after(0, lambda: self._surface_error(exc))
            self.root.after(0, self._after_stop_state)

        threading.Thread(target=_do_stop, daemon=True).start()

    def _after_stop_state(self) -> None:
        self._finalizing = False
        self._recorder = None
        self.var_status.set("Idle — files written")
        self.lbl_status.configure(style="Idle.TLabel")
        self.root.title(f"{APP_NAME} v{__version__}")
        self.var_record_btn.set("Record")
        # Surface output files
        if self._current_paths is not None:
            wav = self._current_paths["wav"]
            txt = self._current_paths["transcript_txt"]
            messagebox.showinfo(
                APP_NAME,
                f"Recording saved.\n\nAudio: {wav}\nTranscript: {txt}\n\nAll files in: {wav.parent}",
            )

    # -------------------- tick + close --------------------

    def _surface_error(self, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        if msg == self._last_error:
            return
        self._last_error = msg
        messagebox.showerror(APP_NAME, msg + "\n\nRecording may have stopped.")
        # If the recorder was lost, drop back to idle state
        if self._recorder is not None and not self._recorder.is_active:
            self._after_stop_state()

    def _tick(self) -> None:
        try:
            if self._recorder is not None and self._recorder.is_active:
                elapsed = int(self._recorder.elapsed_seconds)
                hh, rem = divmod(elapsed, 3600)
                mm, ss = divmod(rem, 60)
                self.var_elapsed.set(f"{hh:02d}:{mm:02d}:{ss:02d}")
                self.var_level.set(min(1.0, max(0.0, self._recorder.level_max)))
            elif not self._finalizing:
                self.var_elapsed.set("00:00:00")
                self.var_level.set(0.0)
        except Exception:
            _log.exception("tick failed")
        finally:
            self.root.after(100, self._tick)

    def _on_close(self) -> None:
        if self._recorder is not None and self._recorder.is_active:
            if not messagebox.askyesno(APP_NAME, "Recording is in progress. Stop and exit?"):
                return
            self._recorder.stop(timeout=5)
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except Exception:
            _log.exception("GUI crashed")
            traceback.print_exc()
            raise


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    App().run()


if __name__ == "__main__":
    run()
