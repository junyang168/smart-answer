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
9. Rebinds and reloads the fellowship-reminder LaunchAgent, then verifies its
   interpreter, script and working directory all belong to the same release
10. Recreates the pm2 app, waits for frontend health, then saves the verified
    release binding for PM2 resurrection after a host reboot
11. On any failure in 8–10, rolls back every release-bound service to the
    previous release automatically and saves that verified rollback binding
12. Writes `.deploy-complete`, `active-release`, and a line in `deployments.log`

`.deploy-complete` means *this release has served healthy traffic*, not *the
build finished*. A release that builds and then fails its health check is
rebuilt on the next attempt rather than reused. It used to be written after the
build, which meant a broken release was cached as complete and retrying could
never recover it.

PM2 resurrection state is written only after the frontend health check passes.
A save failure fails the service switch and triggers rollback rather than
leaving a deployment that works until the next reboot. Deploying an already
active, healthy commit also refreshes the saved state without restarting the
frontend.

### Wang article publication identity

The manual publish and unpublish buttons fail closed unless both the frontend
and backend receive the same two server-only settings:

```dotenv
WANG_PUBLICATION_ACTION_SECRET=<random shared secret>
WANG_PUBLICATION_OWNER_IDS=<comma-separated internal user IDs>
```

Set them in the frontend and backend configuration files under
`/opt/homebrew/var/www/smart-answer-config/`; never prefix the secret with
`NEXT_PUBLIC_`. The frontend derives the actor from the server-side NextAuth
session and signs a short-lived assertion. The backend verifies that signature
and checks the owner allow-list again before recording `decided_by` or
`withdrawn_by`. A missing setting returns 503; do not bypass it by calling the
backend endpoint directly.

### Transcript proofreading beta

The transcript beta is available to registered users assigned `beta_tester` in
`DATA_BASE_DIR/config/config.json`, plus the original owner `junyang168@gmail.com`.
Use `/admin/users` to assign the displayed **beta tester** role. It has the same
permissions as editor, including the single-claim editing rule, and no admin
takeover permission. The owner and admins manage users; testers cannot grant roles.
The Next.js page/proxy and signed backend routes independently check access.
Backend checks use the current central config on every request, including media,
assets, history, mode probes and private publication. The beta data directory's
old config snapshot is not authorization authority. Role removal takes effect on
the next request; existing manuscript/history/claim data is retained. Newly
promoted accounts may need to sign in again to refresh middleware's JWT role.
The beta list is `/admin/transcript-beta`. Ordinary editors retain the legacy entry.

The activated AI draft has a dedicated beta editor at
`/admin/transcript-beta/S201011` and API namespace `/api/sc_api/beta/`.
Its persistent data is selected by backend-only `TRANSCRIPT_REVIEW_BETA_DATA_DIR`,
outside the public web/data tree (production:
`/opt/homebrew/var/www/smart-answer-config/transcript-review-beta/`); legacy
manuscripts, claims and publication files remain outside that root. Beta
publication stays private at `/admin/transcript-beta/S201011/published`.
The beta API is not mounted without the private data-root setting.
The editor, API and publication reader all enforce the same beta-role restriction,
and beta responses are private/no-store. The original port-8000 service was
retired in #591; compatibility API URLs go through the modern Next.js proxy.

Use `scripts/activate-transcript-beta.py --source-root <local-review-data>
--production-root <production-data> --target-root <private-beta-data> --item S201011` with `backend/.venv/bin/python`
to activate the owner's completed AI draft. This refuses an existing beta root,
locks and snapshots the local draft, retains immutable history and source media,
verifies media/assets, records an activation manifest and claims the imported
draft for the owner without adding content review decisions. It does not replace
the existing production manuscript or publish it publicly.

For another lecture, add `--append` with its exact catalog item ID. This only
adds an absent item; it refuses existing manuscripts or review folders. Existing
beta claims and revisions stay intact. Additions are serialized, existing item
workflow locks protect the shared catalog update, and the catalog row is installed
last. Each added item keeps its activation manifest inside its review folder.
Activation compares staged roles against the current production config rather
than a historical beta snapshot; it never replaces existing beta manuscripts or claims.

The full transcription runner also accepts audio-only sources with `--video`
(the CLI argument name is retained for compatibility). It records the original
audio file hash and sample clock, creates an explicitly unavailable, empty visual
inventory, and skips frame detection/OCR. Review media retains its original bytes;
the historical storage filename `source.mp4` does not determine its MIME type.
The authenticated media route uses the candidate's `media_mime_type`, defaulting
to MP4 for older video drafts, and retains HTTP byte-range seeking.

Set `TRANSCRIPT_REVIEW_ACTION_SECRET` to the same randomly generated, server-only
value in the production web `.env.local` and backend LaunchAgent environment.
Never set `TRANSCRIPT_REVIEW_DEV_ACTOR` in production. A deployment installs code;
it does not register, adopt or overwrite a transcript. Registration and adoption
remain explicit, SHA-guarded operations; an existing manually edited or published
transcript must not be replaced by a generated candidate. Before activating a
shared production transcript, reconcile every writer with the migrated-document
guards. Legacy nginx `/sc_api/` URLs now pass through the modern Next.js proxy;
the former port-8000 writer was retired in #591.

### Moving a reviewed Beta draft into production

Use the same ticket worktree and exact catalog item. First make an independent,
verified backup outside both data roots, then promote that snapshot:

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_review_promotion backup --beta-root BETA --production-root DATA --item ITEM --backup-root BACKUP
backend/.venv/bin/python -m backend.pipeline.transcript_review_promotion promote --beta-root BETA --production-root DATA --item ITEM --backup-root BACKUP
```

The backup includes both catalogs, role configuration, every matching script
variant, review sidecars, immutable revisions, operation receipts and media.
Checksums are verified before draft migration. Promotion compares exact item
versions and ownership against that backup, preserves unrelated catalog rows,
and retains a durable intent for interrupted-copy recovery. A different existing
production review draft is never overwritten.

Production receives the Beta review draft, its matching editor baseline,
complete history/media and current owner/assignment epoch. The legacy raw
timeline and public publication stay in place. Prior production baseline and
ownership remain in the verified backup. Review decisions and timing records
are copied without adding approvals; promotion does not publish or update Claims.

A SHA-bound `production-link.json` in the archived Beta review folder makes all
Beta routes for that item use the production store: autosave, claims, history,
media and publication. Other Beta items keep their storage. The backend checks
the configured production root and promotion receipt; a bad handoff fails closed.
A request that selected the old store before the switch is rejected inside its
workflow lock, preserving one writable draft and the existing permission checks.

Deploy router support through `scripts/deploy.sh` before installing the handoff.
The editor URL remains `/admin/transcript-beta/ITEM`; its publish button then
writes production's publication. Publishing still requires actual paragraph and
slide confirmations. A timing hold belonging only to a fully soft-deleted
paragraph does not block publication; missing or unqualified timing for surviving
text still does. Original timing records remain unchanged.

The reviewed publication JSON retains deletion marks as part of its immutable
source snapshot. The public `final_sermon` reader always projects soft deletions
out, including requests with `remove_tags=false`; public Markdown copy and model
consumers receive that same clean payload. Full edit markup remains available
through the authenticated editor/history, not the reader endpoint.

### Python

The interpreter is pinned by `.python-version` (currently `3.13`), read from
the commit being deployed. The script resolves a matching interpreter on the
machine; `SMART_ANSWER_PYTHON` overrides it, and a mismatched version is
rejected.

**Do not build a release venv with a bare `python3`.** This machine has three:

```
/usr/bin/python3           3.9.6
/usr/local/bin/python3     3.12.8   ← separate python.org install, not our test environment
/opt/homebrew/bin/python3  3.13.2   ← production runs this
```

A deploy from a shell with `/usr/bin` first built a 3.9.6 venv. The build
succeeded; the service then died at import on `MicroSermon | None`, which 3.9
cannot evaluate. Nothing before the health check had any reason to object.

Run tests with `backend/.venv`, the pinned 3.13 environment. Root `.venv` is a
compatibility symlink to it; the separate 3.11 development environment was
removed in #591. A bare `python3` still does not select the test interpreter.

### Retiring the pre-release script editor

The old `script_editor_api:app` on port 8000 is separate from the release-bound
backend on 8555. Modern Next.js `/api/sc_api/…` requests already use
`SC_API_SERVICE_URL`, falling back to `FULL_ARTICLE_SERVICE_URL` (8555 in
production). Retiring the old process must preserve the older direct nginx
URLs before disabling its LaunchAgent or uninstalling its Python interpreter.

`config/nginx/retired-script-editor-locations.conf` provides that compatibility:

- `/sc_api/…` proxies through Next.js `/api/sc_api/…`, retaining the current
  session, origin and signed-actor checks rather than exposing the backend
  directly;
- `/public/<item>` redirects to `/resources/sermons/<item>` and `/public`
  redirects to the sermon catalog;
- the obsolete `/public/sitemap` returns 410 (retired);
- `/static/…` serves the preserved legacy assets directly, without Python.

Install this fragment under `/opt/homebrew/etc/nginx/snippets/` and include it
in the production HTTP and HTTPS server blocks, replacing their three old
port-8000 locations. Keep copies and hashes of the original server configs.
Test a separate unprivileged nginx preview with the new fragment first: compare
the API responses to the modern proxy, check redirects/410, and compare static
asset bytes. Validate the entire configuration before reloading it.

The production nginx master runs as root. Editing its user-owned config is not
enough to apply a change: `nginx -t` and `nginx -s reload` require administrator
authentication for its logs, PID file and master process. Until reload and live
checks pass, leave the old 8000 process available. Then disable and unload
`gui/501/script_editor.service`, archive its LaunchAgent plist outside
`~/Library/LaunchAgents`, verify port 8000 and Python 3.11 processes are gone,
and remove the retired virtual environment and Homebrew `python@3.11`. Keep
the old API source, published manuscripts and shared data. A temporary 3.13
replacement for the retired app is not required.

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
| — | Fellowship email reminder, daily schedule check at 10:00 | LaunchAgent `com.smartanswer.fellowshipreminder` | no |
| — | Reference commentary scan inbox, every 3 minutes | LaunchAgent `com.smart_answer.referencecommentaryinbox` | no |
| 3003 | Next.js, older build | not in any deploy path (#78) | via nginx 8888 |
| 8000 | retired in #591; no listener | LaunchAgent disabled and archived | compatibility URLs handled by nginx/Next.js |
| 60000 | legacy QA service | LaunchAgent `smart_answer.service` | `/get_answer` |

Cloudflare Tunnel publishes `https://dallas-hlc.org` (and
`https://www.dallas-hlc.org`) to nginx on port 80; nginx routes `/` to port
3000. The old `holylogos.servehttp.com` hostname is not the production URL and
its certificate expired on 2026-03-03. `:8888` routes `/` to 3003 only on the
local machine.

`smart_answer.service` is in a restart loop (#76): it detaches from launchd
(PPID 1), launchd respawns it, the new instance fails on `address already in
use`, and the cycle writes roughly 8 MB of stderr a day. It had reached 283 MB.
The service itself answers. Do not "fix" it by killing whatever holds port
60000 without reading #76 first.

### Web data directory

The web mostly reads data through the backend, but some places read the disk
directly through `DATA_BASE_DIR`:

- public fellowship documents (`/api/fellowship-documents/…`) and the Markdown
  documents on the fellowship page, from `fellowship/docs`;
- the church website text, from `content/` (#440): `our-faith.md` for
  `/about/our-faith` and `mission-statement.json` for `/` and `/about`. These
  pages read the file on every request, so replacing a file updates the site
  without a deploy. A missing file is a 500 on the home page.

- The backend gets `DATA_BASE_DIR` from its LaunchAgent plist. The web gets it
  from `/opt/homebrew/var/www/smart-answer/web/.env.local`, which every release
  links as `web/.env.local`.
- `scripts/deploy.sh` reads it from that file, refuses to deploy when it is
  missing or not a directory, and passes it to pm2 explicitly. Before #385, pm2
  inherited it from the deploying shell: a deploy on 2026-09-23 from a shell
  without it made every fellowship document return 404 for two days.
- After switching, the deploy downloads one real fellowship document through
  the new web process; if that fails, it rolls back.
- It also refuses to deploy when either file under `content/` is missing.
- In development the web's `.env.local` does not set it; `scripts/dev.sh`
  passes the backend `.env` value to `next dev`.

### Wang staging lives on the Jun SSD

`DATA_BASE_DIR/wang-knowledge-platform/staging` is a symlink to
`/Volumes/Jun SSD/wang-knowledge-platform-staging` (#448, 2026-10-24). On that
date staging held 7.1 GB on an internal disk at 98% and blocked a deploy.

- **It needs the SSD mounted.** Without it the admin article review pages and
  the CVP and Claim pipelines fail on read or write. The public site does not
  read staging.
- **Do not link single subdirectories elsewhere.** The review reader resolves
  the staging root and each artifact path and requires the artifact to lie
  inside the root (`backend/api/wang_article_reviews.py`). A subdirectory linked
  out of the tree resolves outside it and is refused. The whole directory moved
  for that reason.
- The move was verified file by file before the internal copy was removed:
  59,549 files, 10,099 directories and 271 links with identical SHA-256, size,
  mode and link targets. Database dumps that #423 had already moved to the SSD
  stay reachable through their absolute links.

### Website video migration to the Jun SSD

The website media root is `/opt/homebrew/var/www/church/web/video`, alongside
`DATA_BASE_DIR`, rather than inside it. nginx serves it at `/web/video/`;
the canonical repository and original-audio index also read this directory.
`/Volumes/Jun SSD/data/video` is a separate, pre-existing 110 GiB collection
used by slide extraction. Do not overwrite it or assume matching filenames
contain the same bytes.

On 2026-11-02, #593 copied the website directory to
`/Volumes/Jun SSD/church-website-video`: 477 files, 32,475,018,212 bytes,
including 476 audio/video files and `.DS_Store`. Every file's SHA-256, size,
mode, owner and modification time matched. The SSD has ownership disabled,
so its displayed group is `staff` rather than the source's `admin` group.
Extended attributes matched apart from macOS-managed quarantine/provenance
changes introduced by copying; these protections were not removed.

**The website now serves the SSD directory.** The original `video` path is a
symlink to `/Volumes/Jun SSD/church-website-video`, so existing URLs and backend
paths remain the same. The verified internal duplicate has been removed.
The SSD must stay mounted while serving or processing website media.

The first symlink probe returned HTTP 500: nginx logged `Operation not
permitted` while reading the SSD. The owner explicitly authorized enabling
nginx's Full Disk Access and completed the macOS administrator verification.
The same running nginx process then served the SSD successfully, without a
configuration reload, service restart or code deployment. This broad macOS
privacy permission does not add any nginx URL mappings. If the permission is
revoked, or a future nginx update changes the authorized executable, verify
SSD access again before removing any internal fallback copy.

Verification records live outside the public web tree at
`/Volumes/Jun SSD/codex-output/ssd-migration-593/`. They include both file
manifests, metadata differences and the successful pre-migration HTTP HEAD
checks for all 476 media URLs before and after the switch. MP3, MP4 and
Chinese-filename checks verified exact bytes at the beginning, middle and end
of each sampled file (nine HTTP 206 responses). Three additional checks passed
through the public `https://dallas-hlc.org/web/video/` URLs. The cleanup record
confirms the verified internal duplicate was removed after the local checks.

For a future relocation, stage a symlink to the verified destination, retain
the current directory under a temporary name, then switch the original `video`
path to the symlink. Recheck all media URLs and byte-range responses before
removing the duplicate. On failure, restore the retained directory at the
original path. The #593 internal copy has already been removed; reverting this
move requires copying the SSD directory back and verifying it before replacing
the symlink, with sufficient internal free space for the copy. Never unlink
the working media path before the replacement is ready.

### Google OAuth token

Exporting a sermon to a Google Doc uses the owner's own Google sign-in, stored
in `/opt/homebrew/var/www/smart-answer-config/token.json`. Releases do not
contain it.

- The backend finds it through `GOOGLE_OAUTH_TOKEN_FILE` on its LaunchAgent,
  which `scripts/deploy.sh` sets. Refreshed tokens are written back to that file.
- Before switching, the deploy refreshes the token with the new release's code
  (`python -m backend.google_oauth_token check <file>`). If Google refuses it,
  the deploy stops with services unchanged. `--skip-google-token-check` deploys
  anyway; exports then fail with a message saying to sign in again.
- **Signing in again** (after `invalid_grant`). This opens a browser; approve
  with the account that owns the Drive folders:

  ```bash
  cd /opt/homebrew/var/www/smart-answer-config
  cp token.json token.json.bak-$(date +%Y%m%d)
  /Users/junyang/app/smart-answer/.venv/bin/python /Users/junyang/app/smart-answer/generate_user_token.py
  ```

- **Before OPS-27** (#399), the export looked for `token.json` in its working
  directory. That worked while production ran from one fixed directory. Once
  deploys switched to release directories, it found nothing and silently used
  the service account instead. Nothing refreshed the token after 2026-07-28,
  and by September Google had revoked it.
- If the OAuth app in Google Cloud Console is in **Testing** publishing
  status, Google expires refresh tokens after 7 days. "In production" status
  avoids that.

### Fellowship reminder

The fellowship reminder is a separate LaunchAgent, not part of the FastAPI
process. Its plist is
`~/Library/LaunchAgents/com.smart_answer.fellowshipreminder.plist`, while its
label is `com.smartanswer.fellowshipreminder`. The plist retains the production
mail environment and schedule, but all three code-owned paths must be rebound
on every release switch:

- `ProgramArguments[0]` → `<release>/backend/.venv/bin/python3`
- `ProgramArguments[1]` → `<release>/backend/fellowship_reminder_job.py`
- `WorkingDirectory` → `<release>`

Only `scripts/deploy.sh` should perform that rebinding. It unloads and reloads
the reminder without forcing a run, then checks the loaded LaunchAgent rather
than trusting the plist on disk. A missing plist, missing script or loaded-path
mismatch fails the deployment; rollback binds the reminder back to the previous
release along with the web services.

Inspect it without exposing its environment variables:

```bash
plist="$HOME/Library/LaunchAgents/com.smart_answer.fellowshipreminder.plist"
/usr/libexec/PlistBuddy -c 'Print :ProgramArguments' "$plist"
/usr/libexec/PlistBuddy -c 'Print :WorkingDirectory' "$plist"
launchctl print "gui/$(id -u)/com.smartanswer.fellowshipreminder" \
  | sed -n '/^[[:space:]]*program = /p;/^[[:space:]]*working directory = /p'
```

Do not run the reminder with `--force` while diagnosing: that sends mail. The
normal deploy path never passes `--force` and never invokes the reminder job.

### Sunday WhatsApp assistant

The Sunday Presenter website integration and OneDrive PPT storage configuration
are documented in [Sunday Presenter and OneDrive](sunday-presenter.md). Its
server-side configuration is outside Git at
`$DATA_BASE_DIR/config/sunday_presenter.json`. Activate it alongside the reviewed
website change; without it, existing installations retain local PPT storage.
The host's OneDrive Mac client performs upload, so a successful local write is
not a cloud-delivery confirmation. The presenter installer contains no Microsoft
session or host-specific storage configuration.

Prepare its protected shared secret/configuration and LaunchAgent once with
`backend/.venv/bin/python scripts/install-sunday-assistant-agent.py`. This only
writes configuration; it does not load the service or enable collection.
The configuration lives in `smart-answer-config/sunday-assistant.env`; account
state and the durable queue live under `$DATA_BASE_DIR/sunday-assistant/`.
Never copy test forms, queues or group IDs into production.

`deploy.sh` installs the pinned WhatsApp bridge dependencies with browser download
disabled, stops the old supervisor before changing API code, and rebinds the
interpreter, supervisor script and working directory to the active release.
`KeepAlive` and `RunAtLoad` recover the bridge/worker pair after process failure
or user login. Rollback starts the matching supervisor, or unloads it when the
older release has no Sunday assistant. Group posting controls remain persistent
settings; deployment never turns questions or reminders on.

The admin page `/admin/sunday-assistant` shows account, synchronization and worker
health. The supervisor also runs a model-independent administrator incident monitor;
its separate `$DATA_BASE_DIR/sunday-assistant/admin-monitor.sqlite3` retains deduplicated
alerts and delivery receipts across releases. It uses only the configured dedicated
admin group, never a source group; see `docs/sunday-assistant.md` for thresholds.
Log: `~/Library/Logs/smart-answer/sunday-assistant.log`. Check those after
switching; a healthy website alone does not prove WhatsApp is connected.

### Reference commentary scan inbox

The owner scans Carson pages with the iPhone Files app into
`iCloud Drive/Carson/<Book>/ch<N>/`. Every three minutes this LaunchAgent OCRs
new scans with Vertex `gemini-3.8-flash` and records each printed page under
`$DATA_BASE_DIR/reference-commentary/<volume_id>/`. The only file it writes
back to iCloud is `_problems.md` (unreadable page numbers, missing pages).
Design: `docs/wang-knowledge-platform/60-reference-commentary/reference_commentary_solution_v1.md`.

- Plist and label are both `com.smart_answer.referencecommentaryinbox`. It holds
  no secrets: `DATA_BASE_DIR`, the Vertex service-account path and the inbox
  path. Create it once with `scripts/install-reference-inbox-agent.sh`; a
  deploy fails closed while it is missing.
- `scripts/deploy.sh` rebinds and checks it exactly like the fellowship
  reminder (`ProgramArguments[0..1]`, `WorkingDirectory`). Rolling back to a
  release without `backend/reference_commentary_inbox_job.py` unloads it.
- Log: `~/Library/Logs/smart-answer/reference-commentary-inbox.log`.
- A scan is processed once, keyed by SHA-256 in
  `$DATA_BASE_DIR/reference-commentary/inbox-ledger.json`. To redo one, delete
  its ledger entry; page records are never overwritten, so a rerun adds a
  version rather than replacing one.

---

### nginx logs

nginx runs as root (LaunchDaemon `homebrew.mxcl.nginx`) and writes to
`/opt/homebrew/var/log/nginx/`. macOS `newsyslog` rotates every `*.log` there:
at midnight or past 100 MB, 14 gzip copies kept, then nginx gets `SIGUSR1` to
reopen its files. The config is `scripts/ops/newsyslog-nginx.conf`, installed
as `/etc/newsyslog.d/smart-answer-nginx.conf`:

```bash
scripts/install-nginx-log-rotation.sh   # asks for the sudo password; ends with a dry run
```

Until OPS-35 (#421) nothing rotated them: `access.log` reached 3.1 GB on
2026-10-07 with the disk at 98%. To empty a log by hand, truncate it
(`: > access.log`). Deleting it frees nothing while nginx holds it open.

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

**Disk fills.** A release is about 1.25 GB and the deploy now keeps two: the
active one and its rollback target. It reached 98% with four releases retained
and 94% with three, which is what made the retention rule. PostgreSQL is on the
same volume, so a full disk is not only a failed deploy.

---

## Cleaning up releases

`scripts/deploy.sh` prunes after the new release has served healthy traffic,
keeping the active release and its rollback target. Run the same rule by hand
when the disk needs space between deploys:

```bash
scripts/prune-releases.sh --dry-run   # what would go
scripts/prune-releases.sh
```

It refuses when `active-release` is missing or names a tree that is not there,
which is the state a half-finished deploy leaves — in that case decide by hand.
`active-release` names the current release and the last `previous=` field in
`deployments.log` names what it replaced:

```bash
cat /opt/homebrew/var/www/smart-answer-deploy/active-release
tail -2 /opt/homebrew/var/www/smart-answer-deploy/deployments.log
```

Removing anything else is safe — a release is fully derived from one immutable
commit and can be rebuilt.

A release used to be 1.5 GB. The build still needs that much room, but 244 MB
of it is build-only and is removed before the release is retained: the
Turbopack cache under `web/.next/cache` (134 MB), the devDependencies `npm ci`
had to install for the build (111 MB), and `pip` in the release venv (13 MB).
Nothing installs into a release after it is built — an unverified one is
discarded and rebuilt — so none of it comes back.

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

### Sunday speaker Gmail intake

Speaker attachment intake runs in its own bounded thread within the existing
Sunday worker. Configure `$DATA_BASE_DIR/config/sunday_gmail.json` using
`scripts/configure-sunday-gmail.py`, with private per-mailbox tokens outside
website data; authorize each mailbox with `scripts/authorize-sunday-gmail.py`.
This never expands or replaces the shared Drive/Docs token. See
[Sunday assistant Gmail setup](sunday-assistant.md#gmail-私有設定與授權).
Deploying code does not grant Gmail access. The account holder must consent to
read-only access, and the profile must match the configured mailbox before the
worker reads messages. Internal errors join existing admin incident monitoring;
missing consent and ambiguous mail remain setup/review states. Saturday 22:00
Dallas speaker-PPT follow-up has an independent admin-group-only switch and does
not enable source-group questions or Friday reminders.

### Fellowship preparation in the Sunday assistant

The `fellowship_enabled` setting adds the dedicated administrator group to
`ingest_groups`, while leaving the three source-group roster/audit unchanged.
Activation records `fellowship_since` and `fellowship_start_date`; only new
administrator messages are eligible. The Sunday 15:00 request and the following
Thursday 09:00 missing-information reminder use Dallas time and a separate
admin-only `fellowship:` envelope. See [fellowship workflow](sunday-assistant.md#雙週讀經團契).

A separate bounded thread runs the existing release-bound fellowship reminder
script with `--catch-up`, borrowing only its current LaunchAgent's mail/data
configuration and checking that data roots agree. There is no new LaunchAgent
and the existing daily reminder schedule is retained. Catch-up stops before
Friday 19:30. The per-event delivery ledger under
`DATA_BASE_DIR/notification/fellowship-delivery/` is private and outside releases;
retain it with `last_sent.json` across deploys. Do not delete a `sending` or
`unknown` reservation to make a retry happen: SMTP may already have accepted
mail. Verify provider-side delivery first. Administrator incident monitoring
reports unresolved delivery failures; ordinary missing inputs are business
pending states. Normal deploys must not interrupt an in-flight old reminder
process before its legacy sent marker is recorded.
