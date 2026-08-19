# Operations runbook

The shape of the production machine, for whoever operates it next.

This exists because on 2026-08-18 an agent had to derive all of it by inspection
and got it wrong twice: it reached for an entry point that deploys nothing, and
it built a release against the wrong Python. Both mistakes are cheap to repeat
and expensive to diagnose. Read this before touching production.

`docs/infrastructure.md` describes request routing. This file describes
deploying and operating.

---

## The three directories

Only one of them holds the code that is running. The one whose name reads like
production does not.

| Path | What it is |
|---|---|
| `/opt/homebrew/var/www/smart-answer-deploy/releases/<sha>/` | **The running code.** One immutable directory per deployed commit. |
| `/opt/homebrew/var/www/smart-answer-config/` | **Configuration and runtime data.** `.env`, `backend/.env`, `web/.env.local`, `web/data/`, `service_account.json`, `client_secret.json`, `token.json`. Every release symlinks into it. Not in Git, and there is no other copy. |
| `/Users/junyang/app/smart-answer/` | The development checkout. **Deploys are launched from here**, and so are the query tools. |

`/opt/homebrew/var/www/smart-answer` is a compatibility symlink to
`smart-answer-config`. Releases built before 2026-08-18 symlink through that
path; the rollback target may still be one of them, so do not remove it while
any release older than `a5f56ca` is retained.

Until 2026-08-18 that path was a Git checkout *and* the configuration store at
the same time — one directory meaning both "must not be touched" and "pull
whenever you like". It drifted 17 commits behind with 105 uncommitted changes,
and a `git reset --hard` there missed the configuration only because every
config file happened to be untracked. Do not recreate that arrangement.

---

## Deploying

There is one entry point:

```bash
cd /Users/junyang/app/smart-answer
scripts/deploy.sh --dry-run     # resolve and validate, change nothing
scripts/deploy.sh               # deploy origin/main
```

Options: `--ref <commit>` for a specific commit, `--allow-non-main` to permit a
commit not contained in `origin/main`.

There is no other deploy script. Four used to exist — a root `deploy.sh`, a
`deploy_search.sh`, `scripts/deploy_backend.sh`, `scripts/deploy_nextjs.sh` —
all doing `git pull` plus `launchctl unload/load` against the old directory.
Under release-based deployment they put no new code anywhere while mutating the
directory production depends on. They were deleted in #80. **If you find
yourself writing one, you are about to repeat the mistake that made PR #54
appear to deploy while nothing changed.**

`scripts/deploy.sh` is run out of the development checkout, not out of a
release, so a change to the script itself takes effect immediately and does not
need its own deploy.

### What a deploy does

1. Refuses a commit not in `origin/main` unless `--allow-non-main`
2. Refuses if free space is below `SMART_ANSWER_MIN_FREE_MB` (default 6144)
3. Takes a lock at `$DEPLOY_ROOT/.deploy-lock`
4. `git archive` of the target commit into `releases/<sha>` — uncommitted and
   untracked files in the checkout cannot reach production
5. Symlinks the config files, writes `release.json`
6. Builds `backend/.venv` with the pinned interpreter, then `npm ci` and
   `npm run build`
7. Blocks on any critical finding from `npm audit --omit=dev`
8. Restarts the backend LaunchAgent, waits for health, **asserts the backend
   reports the commit just deployed**
9. Recreates the pm2 app, waits for frontend health
10. On any failure in 8–9, rolls back to the previous release automatically
11. Writes `.deploy-complete`, `active-release`, and a line in `deployments.log`

`.deploy-complete` means *this release has served healthy traffic*, not *the
build finished*. A release that builds and then fails its health check is
rebuilt on the next attempt rather than reused. It used to be written after the
build, which meant a broken release was cached as complete and retrying could
never recover it.

### Python

The interpreter is pinned by `.python-version` (currently `3.13`), read from
the commit being deployed. The script resolves a matching interpreter on the
machine; `SMART_ANSWER_PYTHON` overrides it, and a mismatched version is
rejected.

**Do not build a release venv with a bare `python3`.** This machine has three:

```
/usr/bin/python3           3.9.6
/usr/local/bin/python3     3.12.8   ← the test suite runs on this
/opt/homebrew/bin/python3  3.13.2   ← production runs this
```

A deploy from a shell with `/usr/bin` first built a 3.9.6 venv. The build
succeeded; the service then died at import on `MicroSermon | None`, which 3.9
cannot evaluate. Nothing before the health check had any reason to object.

Note that the test suite and production are still on different minor versions.
Running the suite on 3.13 would settle it; until then, a green test run is not
proof for production.

---

## Answering "what is running"

```bash
curl -s http://127.0.0.1:8555/healthz
# {"status":"ok","release":"a5f56ca…","deployed_at":"2026-08-19T00:06:21Z"}
```

Localhost only — the backend binds `127.0.0.1` and no nginx location proxies to
it.

```bash
cd /Users/junyang/app/smart-answer
scripts/deployed-issues.sh            # vs origin/main
scripts/deployed-issues.sh <ref>      # vs any ref
```

It reports what the current release brought (measured against the release it
replaced) and what is merged but not live, resolving issues through GitHub's
`closingIssuesReferences`.

**A pull request must declare `Closes #N` before it merges.** GitHub does not
create the link on an already-merged pull request, so an issue linked after the
fact is invisible to this tool — #56, #57, #58 and #62 were all delivered by
PR #54 and none of them can be found this way. Reading commit subjects for
`#123` is not a substitute: it cannot distinguish a ticket a commit closed from
one it mentioned in passing, and one release listed seven still-open issues
that way. `.github/pull_request_template.md` carries the rule.

---

## Services and ports

| Port | Service | Managed by | Public |
|---|---|---|---|
| 8555 | FastAPI backend, `backend.api.main:app` | LaunchAgent `com.smart_answer.fullarticleservice` | no |
| 3000 | Next.js frontend | pm2 app `smart-answer` | yes, via nginx 443/80 |
| 3003 | Next.js, older build | not in any deploy path (#78) | via nginx 8888 |
| 8000 | legacy backend | processes dating from 2026-07-13 | `/sc_api/`, `/public`, `/static` |
| 60000 | legacy QA service | LaunchAgent `smart_answer.service` | `/get_answer` |

nginx: `holylogos.servehttp.com:443` and `:80` route `/` to port 3000;
`:8888` routes `/` to 3003.

`smart_answer.service` is in a restart loop (#76): it detaches from launchd
(PPID 1), launchd respawns it, the new instance fails on `address already in
use`, and the cycle writes roughly 8 MB of stderr a day. It had reached 283 MB.
The service itself answers. Do not "fix" it by killing whatever holds port
60000 without reading #76 first.

---

## Things that will bite

**Ambient shell state changes the outcome.** The Python failure above came from
`PATH` order. An agent's shell is not the operator's shell. Prefer explicit
paths and the pinned interpreter over whatever `command -v` finds.

**Secrets are readable by anything running as this user.** The LaunchAgent
plist holds ANTHROPIC, GEMINI, DEEPSEEK and SCRIPTURE API keys plus SMTP
credentials. It is mode 600 and the deploy re-applies that, but every agent on
this machine runs as the same user and can read it. Tracked as #74; treat it as
live until closed.

**Logs are in `/tmp` with no rotation** (#77). `/tmp` is cleared, so the error
from last week is probably gone. Do not plan a diagnosis around log history.

**Disk fills.** A release is about 1.5 GB, and cleanup is manual. The volume
reached 98% with four releases retained. PostgreSQL is on the same volume.

---

## Cleaning up releases

Keep the active release and the rollback target. `active-release` names the
current one; the previous line of `deployments.log` names what it replaced.

```bash
cat /opt/homebrew/var/www/smart-answer-deploy/active-release
tail -2 /opt/homebrew/var/www/smart-answer-deploy/deployments.log
```

Removing anything else is safe — a release is fully derived from one immutable
commit and can be rebuilt. There is no pruning script yet; this is judgement,
which is exactly why it should become one.

---

## Backups taken during the 2026-08-18 cleanup

Under `$DATA_BASE_DIR/wang-knowledge-platform/deployment-backups/`:

- `prod-worktree-20260818/` — the old checkout's 105 uncommitted changes as a
  patch, plus the four files that were never in Git, with restore steps
- `legacy-orphans-20260818/` — `content_store/`, `backend/full_article_service/`
  and `backend/api/multi_agent/`, which existed in no commit, no release and no
  import, and had no other copy

---

## Open operational tickets

`gh issue list --label infrastructure`

`OPS-` numbering and the `infrastructure` label are for machine operations.
Content and knowledge-platform work uses `WKP-` and carries no label. Keeping
them apart matters: a mixed list is unreadable, and the two have different
reviewers and different risk.
