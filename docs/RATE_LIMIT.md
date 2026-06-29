# Rate-limit fallback plan — `MeetingRecorder`

Carson has run into rate limits during previous GitHub pushes (notably during the Hermes-V1 / NAS deploys). The plan below keeps the deliverable intact even if the GitHub API hits its limit mid-publish.

## TL;DR

There are three publish paths. They run **in order**, each one aware of the previous attempt's result, and only produce work the next path can use. The script that drives this is `scripts/publish.sh`.

| Path | Pre-condition | Action | Output |
|------|--------------|--------|--------|
| 1 — `gh repo create` + `git push` | GitHub token has `repo` scope AND primary rate limit has remaining budget | POST `/user/repos`, push the source tree | Live repo at `github.com/<owner>/meeting-recorder` |
| 2 — `curl` REST create + `git push` | Same as path 1 but using the GitHub REST API directly (bypasses `gh`) | `POST /user/repos` via `curl`, push via `git` over HTTPS using the token | Same as path 1 |
| 3 — Tarball + local mirror | Rate limit hit, network down, or scope missing | Build `dist/meeting-recorder-<commit>.tar.gz`, write a SHA-256 manifest, copy to `/mnt/nas/neo/meeting-recorder/` and `/mnt/nas/ketrow-family/Neo Infrastructure/meeting-recorder/` | Local archive, no remote repo required |

Every path tries to keep the **commit history exactly as authored**, so any later `git push` from a clean machine will succeed without conflicts.

## Detection

`scripts/publish.sh` checks, in this order:

1. Is `GITHUB_TOKEN` set? If not → branch straight to path 3.
2. Is `gh auth status` successful AND `gh repo view <repo>` returns 200? If not → try path 2.
3. Hits `GET /rate_limit` and reads `remaining` for the core tier. If `< 5`, → path 3.
4. Otherwise attempt path 1.
5. If path 1 returns HTTP 403 with `X-RateLimit-Remaining: 0` or `Retry-After` set, → path 3.

## Idempotency

- If the repo already exists at `github.com/<owner>/meeting-recorder`, path 1/2 treat that as success (no double-create).
- Tarballs are deduplicated by commit SHA.
- Local mirror copies are atomically replaced, never duplicated.

## What you can do to recover

- Wait for the rate-limit window to reset (the script prints the exact reset time).
- Create the repo manually from the GitHub UI, then re-run `scripts/publish.sh` and it will skip creation and push directly.
- If nothing else works, the tarball in `dist/` plus the local NAS copy ARE the deliverable; the source remains a normal working tree you can push any time later.

## Why this design

- Same code path regardless of which mechanism pushed (gh vs REST vs offline).
- Zero silent failures: every path either succeeds or logs a clear next action.
- No interactive prompts — fully unattended, suitable for cron.
- Honors the agent's read-first / change-gated network policy: it never pulls from upstream without explicit verification.
