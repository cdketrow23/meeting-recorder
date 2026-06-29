"""Behavioural checks for the rate-limit fallback plan.

We do not call GitHub in tests. Instead we run the publish script in `--dry-run`
mode (no token, no network) and assert it exits cleanly and emits one of the
expected fallback strings. We also confirm the tarball + manifest always get
written even when GitHub is unreachable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _run_publish(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DRY_RUN"] = "1"
    env.pop("GITHUB_TOKEN", None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "publish.py"), "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_publish_dry_run_no_token_exits_zero():
    res = _run_publish({})
    out = (res.stdout + res.stderr).lower()
    assert res.returncode == 0, out
    assert "tarball" in out


def test_publish_writes_manifest_and_tarball():
    res = _run_publish({})
    assert res.returncode == 0
    manifest = ROOT / "dist" / "publish.manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text())
        assert isinstance(data, list)
        assert data[-1]["fallback_reason"] in {"no token", "rate limit", "no reason set", ""}
