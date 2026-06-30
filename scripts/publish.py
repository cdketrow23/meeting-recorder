#!/usr/bin/env python3
# Publish the MeetingRecorder source tree to GitHub with rate-limit fallback.
# Falls back to a tarball + NAS mirror if GitHub is unavailable.
# See ../docs/RATE_LIMIT.md for full plan.
# Run:
#     GITHUB_TOKEN=YOURTOKEN ./scripts/publish.py [--dry-run] [--repo OWNER/NAME]
# Idempotent: re-running with existing repo or already-published state is safe.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
LOG_FILE = DIST / "publish.manifest.json"
DEFAULT_REPO_SLUG = "cdketrow23/meeting-recorder"

NAS_MIRRORS = [
    Path("/mnt/nas/neo/meeting-recorder"),
    Path("/mnt/nas/ketrow-family/Neo Infrastructure/meeting-recorder"),
]

ANSI_BLUE = "\033[1;34m"
ANSI_RED = "\033[1;31m"
ANSI_YELLOW = "\033[1;33m"
RESET = "\033[0m"


def log(msg):
    sys.stdout.write(ANSI_BLUE + "[publish]" + RESET + " " + msg + "\n")
    sys.stdout.flush()


def warn(msg):
    sys.stderr.write(ANSI_YELLOW + "[publish]" + RESET + " " + msg + "\n")
    sys.stderr.flush()


def err(msg):
    sys.stderr.write(ANSI_RED + "[publish]" + RESET + " " + msg + "\n")
    sys.stderr.flush()


def load_token():
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    candidates = [
        Path.home() / ".hermes" / ".env",
        Path.home() / ".hermes" / "secrets" / "github.env",
    ]
    for p in candidates:
        if not p.is_file():
            continue
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line.startswith("GITHUB_TOKEN"):
                continue
            _, _, val = line.partition("=")
            val = val.strip()
            if val and val[0] == val[-1] and val[0] in ("\'", "\"", "`"):
                val = val[1:-1]
            if val:
                return val
    return ""


def run_git(*args, check=True):
    res = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    return res


def git_init_or_open():
    if not (ROOT / ".git").exists():
        run_git("init", "-q", "-b", "main")
    name = os.environ.get("GIT_AUTHOR_NAME") or "CDKetrow Builder"
    email = os.environ.get("GIT_AUTHOR_EMAIL") or "[email protected]"
    run_git("config", "user.name", name, check=False)
    run_git("config", "user.email", email, check=False)


def git_commit_all(message):
    run_git("add", "-A")
    run_git("commit", "-q", "-m", message, check=False)


def build_tarball(short_sha):
    DIST.mkdir(parents=True, exist_ok=True)
    tarball = DIST / ("meeting-recorder-" + short_sha + ".tar.gz")
    log("Building " + str(tarball))
    if tarball.exists():
        tarball.unlink()
    parent_name = ROOT.name
    skip = {"dist", ".venv", ".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
    with tarfile.open(tarball, "w:gz") as tf:
        for entry in sorted(ROOT.iterdir()):
            if entry.name in skip:
                continue
            tf.add(entry, arcname=parent_name + "/" + entry.name, recursive=True)
    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    (tarball.with_suffix(tarball.suffix + ".sha256")).write_text(sha + "  " + tarball.name + "\n")
    return tarball, sha


class GitHub:
    def __init__(self, token):
        self.token = token

    def _req(self, method, url_path, payload=None):
        url = "https://api.github.com" + url_path
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "meeting-recorder-publish",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                txt = resp.read()
                try:
                    data = json.loads(txt or b"{}")
                except Exception:
                    data = {}
                return resp.status, data, dict(resp.getheaders())
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(txt)
            except Exception:
                data = {}
            return e.code, data, dict(e.getheaders() or {})

    def probe_rate_limit(self):
        code, data, _ = self._req("GET", "/rate_limit")
        if code != 200:
            return code, None
        return code, int(data.get("rate", {}).get("remaining", -1))

    def repo_exists(self, slug):
        code, _, _ = self._req("GET", "/repos/" + slug)
        return code == 200

    def create_repo(self, slug):
        name = slug.split("/", 1)[-1]
        code, _, _ = self._req(
            "POST",
            "/user/repos",
            {
                "name": name,
                "private": False,
                "description": "MeetingRecorder - local audio recorder and transcriber",
                "auto_init": False,
            },
        )
        return code

    def push(self, slug, exclude_paths=None):
        """git push via HTTPS.

        If ``exclude_paths`` is given (list of repo-relative paths), the
        commit with those paths is rebased out of the tip before pushing,
        then the original commit is restored locally. This handles the case
        where the token lacks scopes GitHub enforces on commit content
        (e.g. ``workflow`` for .github/workflows/*.yml).
        """
        url = "https://oauth2:" + self.token + "@github.com/" + slug + ".git"
        run_git("remote", "remove", "origin", check=False)
        run_git("remote", "add", "origin", url, check=False)

        excluded_summary = ""
        if exclude_paths:
            head_sha = run_git("rev-parse", "HEAD").stdout.strip()
            # Make a backup branch so we can restore later
            backup_branch = "mr-pre-workflow-strip"
            run_git("branch", "-f", backup_branch, "HEAD", check=False)
            run_git("checkout", "-q", backup_branch, check=False)
            # Remove the offending paths, amend the commit
            run_git("rm", "-rq", "--", *exclude_paths, check=False)
            run_git("commit", "-q", "--amend", "--no-edit", check=False)
            new_sha = run_git("rev-parse", "HEAD").stdout.strip()
            excluded_summary = "stripped " + ", ".join(exclude_paths) + " from tip (was " + head_sha[:12] + ", now " + new_sha[:12] + ")"

        res = run_git("push", "-u", "origin", "main", "--force-with-lease", check=False)
        ok = res.returncode == 0
        full_log = ((res.stderr or "") + "\n" + (res.stdout or "")).strip()
        # Keep the last few lines so the reason is visible; fall back to "" if all blank
        err_msg = ""
        if not ok:
            nonblank = [ln for ln in full_log.splitlines() if ln.strip()]
            err_msg = nonblank[-1] if nonblank else "push failed"
            err_msg_full = "\n".join(nonblank[-6:])  # last 6 lines for diagnostics
        else:
            err_msg_full = ""
        # Always restore the local tree state
        if exclude_paths:
            run_git("checkout", "-q", "main", check=False)
        run_git("remote", "remove", "origin", check=False)
        return ok, err_msg, err_msg_full, excluded_summary


def main():
    parser = argparse.ArgumentParser(description="Publish MeetingRecorder with rate-limit fallback.")
    parser.add_argument("--dry-run", action="store_true", help="Skip network and git push.")
    parser.add_argument("--repo", default=os.environ.get("REPO_SLUG", DEFAULT_REPO_SLUG))
    args = parser.parse_args()

    slug = args.repo
    dry_run = args.dry_run or (os.environ.get("DRY_RUN", "0") not in ("", "0", "false"))

    try:
        git_init_or_open()
    except subprocess.CalledProcessError as exc:
        err("git init/commit failed: " + str(exc))
        return 2

    git_commit_all(
        "chore: publish source tree at "
        + datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    short_sha = run_git("rev-parse", "--short=12", "HEAD").stdout.strip()
    if not short_sha:
        err("Could not read commit SHA; aborting.")
        return 2

    tarball, sha = build_tarball(short_sha)
    log("Tarball SHA256: " + sha)

    token = load_token()
    pushed = False
    reason_fallback = ""

    if not token:
        warn("No GITHUB_TOKEN found; falling back to local-only archive.")
        reason_fallback = "no token"
    elif dry_run:
        log("DRY_RUN: would attempt GitHub repo creation + push.")
    else:
        gh = GitHub(token)
        code, remaining = gh.probe_rate_limit()
        if code != 200 or (remaining is not None and remaining < 5):
            warn("Rate-limit probe (HTTP " + str(code) + ", remaining=" + str(remaining) + "). Falling back.")
            reason_fallback = "rate limit"
        else:
            existing = gh.repo_exists(slug)
            if not existing:
                code_create = gh.create_repo(slug)
                if code_create not in (200, 201):
                    warn("Repo create failed (HTTP " + str(code_create) + "); falling back.")
                    reason_fallback = "create failed " + str(code_create)
                else:
                    log("Created https://github.com/" + slug)
            else:
                log("Repo " + slug + " already exists; pushing.")
            ok, err_msg, err_msg_full, excluded_summary = gh.push(slug)
            workflow_stripped = excluded_summary if excluded_summary else ""
            if ok:
                pushed = True
                log("Push complete: https://github.com/" + slug)
                if workflow_stripped:
                    warn("Note: " + workflow_stripped)
                    warn("The .github/workflows/*.yml files require a token with the 'workflow' scope. They are in the local commit and the tarball, but NOT on the remote until the scope is added.")
            else:
                # Use the full multi-line message for matching the workflow-scope rejection
                emsg = (err_msg_full or err_msg).lower()
                if "rate limit" in emsg or "secondary rate" in emsg:
                    reason_fallback = "rate limit"
                    warn("Push failed: " + err_msg + " (full: " + err_msg_full + ")")
                elif "workflow" in emsg and ("personal access token" in emsg or "workflow scope" in emsg or "refusing" in emsg):
                    # Token lacks the `workflow` scope. Try again with the offending paths held back.
                    warn("GitHub rejected because the token lacks the 'workflow' scope. Retrying without .github/workflows/*.")
                    warn("Original rejection:")
                    for ln in (err_msg_full or "").splitlines():
                        warn("  " + ln)
                    ok2, err_msg2, err_msg_full2, excluded_summary2 = gh.push(slug, exclude_paths=[".github/workflows/build-windows.yml", ".github/workflows/lint.yml"])
                    if ok2:
                        pushed = True
                        reason_fallback = "workflows stripped (token missing 'workflow' scope)"
                        if excluded_summary2:
                            warn("Note: " + excluded_summary2)
                        log("Push complete (workflows omitted): https://github.com/" + slug)
                    else:
                        reason_fallback = "push failed (retry): " + err_msg2
                        warn("Retry failed: " + err_msg2)
                else:
                    reason_fallback = "push failed: " + err_msg
                    warn("Push failed: " + err_msg + " (full: " + err_msg_full + ")")

    for m in NAS_MIRRORS:
        try:
            m.mkdir(parents=True, exist_ok=True)
            shutil.copy2(tarball, m / tarball.name)
            shutil.copy2(tarball.with_suffix(tarball.suffix + ".sha256"), m / (tarball.name + ".sha256"))
            log("Mirrored to " + str(m))
        except Exception as exc:
            warn("Mirror to " + str(m) + " skipped: " + str(exc))

    DIST.mkdir(parents=True, exist_ok=True)
    history = []
    if LOG_FILE.exists():
        try:
            old = json.loads(LOG_FILE.read_text())
            if isinstance(old, list):
                history = old
        except Exception:
            history = []
    history.append({
        "commit": short_sha,
        "tarball": str(tarball),
        "tarball_name": tarball.name,
        "sha256": sha,
        "repo": slug,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "github_pushed": pushed,
        "github_url": ("https://github.com/" + slug) if pushed else None,
        "fallback_reason": reason_fallback,
    })
    LOG_FILE.write_text(json.dumps(history, indent=2))
    log("Manifest: " + str(LOG_FILE))

    if pushed:
        log("PUSHED: https://github.com/" + slug)
    else:
        warn("FALLBACK (no GitHub push): " + (reason_fallback or "no reason set"))
        warn("Tarball: " + str(tarball))
        warn("SHA-256: " + sha)
    log("Tarball always at: " + str(tarball))
    return 0


if __name__ == "__main__":
    sys.exit(main())
