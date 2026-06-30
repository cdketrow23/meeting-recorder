from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_spec_uses_top_level_entrypoint():
    """PyInstaller must not execute package __main__.py directly.

    Running src/meetingrecorder/__main__.py as a script breaks relative imports:
    `from .app import run` raises "attempted relative import with no known parent
    package". The spec must point at the top-level shim instead.
    """
    spec = (ROOT / "meetingrecorder.spec").read_text(encoding="utf-8")
    assert '"pyinstaller_entry.py"' in spec
    assert '"src/meetingrecorder/__main__.py"' not in spec


def test_pyinstaller_entry_imports_package_main():
    source = (ROOT / "pyinstaller_entry.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "from meetingrecorder.__main__ import main" in source
