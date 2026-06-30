# PyInstaller spec for MeetingRecorder (Windows one-file build).
# Run on Windows with:
#   pip install -r requirements.txt
#   pyinstaller meetingrecorder.spec --noconfirm
#
# The result is a single executable at dist\MeetingRecorder.exe.

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

datas = []
# Bundling the optional Vosk model is intentionally OFF. See docs/HOW_TO_USE.md.
# If you want a fully offline build, download the small English model from
# https://alphacephei.com/vosk/models and set VOSK_MODEL to its folder path.

hiddenimports = [
    "sounddevice",
    "soundfile",
    "numpy",
    "vosk",
    "tkinter",
]

a = Analysis(
    ["pyinstaller_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter.test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MeetingRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
