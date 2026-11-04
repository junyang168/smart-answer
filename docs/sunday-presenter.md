# Sunday Presenter and OneDrive (#590)

Umbrella epic: [SSA-E12 — Sunday Service Agent (#658)](https://github.com/junyang168/smart-answer/issues/658).
The long-term goal is full automation with human review and approval; Presenter
and iPad Remote currently establish the Foundation. Preparation and rollout
boundaries are recorded in [Sunday Service Agent goals](sunday-assistant.md#sunday-service-agent-的目標與追蹤).

Current delivery: #598 is Phase 1 (0.4.0), with PPT playback and local speaker
slide control. Google Meet joining, sharing, microphone and recording are manual.
Phase 2 #643 adds YouTube broadcasting and microphone relay inside Sunday Remote.
Phase 1 removes the old Meet Accessibility integration and automatic 10:00 start;
old service settings do not execute. See tools/sunday-presenter/README.md.

The website owns the date and OneDrive folder mapping. The desktop app calls
`GET /api/admin/sunday-services/presenter/upcoming` on launch, retry and folder
open. The existing Next.js Sunday-services proxy forwards to the backend's
`/admin/sunday-services/presenter/upcoming`. Responses are `no-store`.

The API returns `date`, `timezone`, `folderName`, `folderUrl`, `folderExists`,
`storage: onedrive-sync` and `cloudStatus: not_verified`; it never returns the
local filesystem path. This uses the existing Sunday-services API's access
policy and returns an ordinary OneDrive folder URL, not a sharing token or new
permission. Microsoft still requires the user's access to the files.

The date is the next Sunday including today, in `America/Chicago`, independent
of browser/laptop timezone or the existence of a service record. The directory
name is unpadded `month-day-year`. A missing week never falls back to an old one.
GET does not create directories. Generating/uploading a PPT creates the exact
date directory before saving. A missing OneDrive root is an error, not permission
to manufacture a disconnected replacement.

## Configure the website host

Production runs on the Mac with the existing OneDrive Personal sync client.
The owner supplied its `Documents/主日` sync directory. Configuration is outside
Git, by default `$DATA_BASE_DIR/config/sunday_presenter.json`:

```json
{
  "syncRoot": "/absolute/path/to/OneDrive-Personal/Documents/主日",
  "webRootUrl": "https://onedrive.live.com/my?id=%2Fpersonal%2FACCOUNT%2FDocuments%2FDocuments%2F%E4%B8%BB%E6%97%A5"
}
```

Use an actual existing dated folder URL to preserve the account's web path,
which can differ from its local path:

```sh
backend/.venv/bin/python scripts/configure-sunday-presenter.py \
  --root '/absolute/path/to/OneDrive-Personal/Documents/主日' \
  --sample-folder-url 'https://onedrive.live.com/my?id=ACTUAL_DATED_FOLDER_ID'
```

`--output` selects an alternate file. `SUNDAY_PRESENTER_CONFIG` selects that file
for a development backend. Configure production only as part of activating the
reviewed website change; deployment itself follows `docs/operations-runbook.md`.
Without configuration existing installs retain their old local storage, and
the upcoming-folder API returns 503. Invalid configuration or an unavailable
configured sync root also returns an error; it does not silently save locally.

No Graph application, copied browser cookies or new OAuth permission is needed.
The signed-in OneDrive Mac client performs cloud upload. A newly written file
may still be syncing or blocked; verify in OneDrive before service. Official
[OneDrive status documentation](https://support.microsoft.com/en-us/onedrive/what-do-the-onedrive-icons-mean)
describes sync progress and errors.

## Files and existing data

Generated output: `month-day-year/主日崇拜-YYYY-MM-DD-自動生成.pptx`.
Uploaded final output retains its existing filename:
`month-day-year/聖道教會YYYY-MM-DD主日崇拜.pptx`. Regeneration replaces only the
generated file, leaving the human final and speaker's files alone. Rendering
uses a private temporary file, then replaces a complete destination. A failed
copy preserves the previous complete generated PPT.

The automatic assistant retains its immutable versioned artifact locally for
input/hash validation, then publishes the validated bytes to OneDrive before
marking the job ready or queueing its existing notification. Superseded render
inputs do not publish. The helper does not add a notification or send a message.

Final downloads and existing email attachment preparation first read OneDrive;
old local files remain readable as a compatibility fallback for historical
uploads. Service JSON, workers, songs and template files remain in the website's
data directory. Only PPT deliverables move to the dated OneDrive folders.

Historical migration is read-only by default:

```sh
backend/.venv/bin/python scripts/migrate-sunday-ppts.py \
  --source "$DATA_BASE_DIR/sunday_worship" \
  --manifest output/sunday-presenter/migration-plan.json
```

Review the manifest, then add `--apply` to copy. All destination conflicts are
checked before writing; identical copies can be rerun. Byte hashes are checked
after copying. Templates, Office lock files and JSON are excluded. Originals
remain as recovery copies; the script never deletes or overwrites a different
OneDrive destination. Manifests contain private paths and stay outside Git.

## Development and packaging

Run this worktree's `scripts/dev.sh` with its private config override. For #590
the website is `http://localhost:3590` and backend `http://127.0.0.1:9590`.
`SUNDAY_PRESENTER_API_URL` can direct a source/packaged app to the local website
API for testing. The distributable default is always `https://dallas-hlc.org`.
The app rejects insecure external API endpoints and validates the returned
date, OneDrive host and dated folder before allowing it to open.

From `tools/sunday-presenter`, `npm run icons` builds the native `.icns` from
the tracked vector using the repository's existing sharp installation.
`npm run package:mac` builds an arm64 `.app`, DMG and ZIP. The minimum macOS
version is 13, as declared by the packaged Electron runtime. Installers contain
no account sessions or host-specific config. Version 0.3.0 is ad-hoc signed,
without Apple Developer ID or notarization, and also builds a universal native
Sunday Remote for the speaker's Mac.

## Authenticated installer downloads

The Sunday-service admin page provides the Presenter arm64 DMG and Remote
universal ZIP. Both metadata and file routes require an editor-capable session
in production. Development uses the site's existing development-session helper.
The installer route streams bytes in Next rather than buffering a large file in
the generic backend proxy. Its explicit self-rewrite must precede that proxy's
catch-all; otherwise dynamic download URLs reach the Python backend and 404.

Build and inspect the packages before publishing their immutable version. The
0.4.0 Phase 1 artifacts come from the reviewed Presenter checkout in #598 / PR #611.
Production publication is scoped to playback and slide control, not automated audio.

```sh
cd tools/sunday-presenter
npm run package:mac
cd ../..
node scripts/publish-sunday-presenter.cjs --version 0.4.0 --status released \
  --dist /path/to/reviewed/presenter/dist \
  --root "$DATA_BASE_DIR/sunday-presenter/releases"
```

The publisher copies only the two allowlisted installers, reads their SHA-256
back, and atomically updates `latest.json` after both are complete. Existing
version bytes cannot be replaced; changed builds need a new package version.
Installers live outside Git and release-code directories. The website validates
the manifest, filenames, lengths and regular-file boundaries before serving.
No private Meet link, OneDrive configuration, paired identity or login session
is included. The entry labels released builds as Phase 1 and older pilot builds as testing.
Only pilot/released statuses are accepted. Publish released metadata after deploying
the matching website reader; earlier deployed readers accept only pilot. Neither
status claims Phase 2 broadcast or automated-service acceptance.
