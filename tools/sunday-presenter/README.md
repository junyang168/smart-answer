# Sunday Presenter — Mac prototype (#590)

Two original PowerPoint presentations, one persistent audience window. This is
a packaged Electron app. At startup it calls the church website's upcoming-Sunday
API for the dated OneDrive folder. The website saves newly generated and uploaded
PPTs into that same OneDrive sync tree. It does not send church group messages.

**Status: technical prototype. The owner's Microsoft sign-in and two real slide
shows work; a second signed-in Meet client received switching in the windowed
baseline, and the owner subsequently reported successful full-screen sharing.
Media/audio and broader animation acceptance remain separate checks.** See
[acceptance.md](acceptance.md) for observations and the remaining checks.

## Install on another Mac

Use `dist/Sunday-Presenter-0.3.0-mac-arm64.dmg` on an Apple Silicon Mac running
macOS 13 or newer. Drag **Sunday Presenter** into **Applications**. A ZIP of the
app is also produced. Node.js is not needed on the receiving laptop. The package
contains an independent app icon, but no Microsoft account/session or private
folder configuration. Sign in inside the app on each Mac.

This test build has an ad-hoc signature, without Developer ID or notarization; macOS may require **System
Settings → Privacy & Security → Open Anyway** after the first blocked launch.
The website integration is deployed; manual
PowerPoint links remain usable while the website is unavailable.

## Paired remote and Meet workflow prototype (#598)

The DMG also contains **Sunday Remote**, a native universal Intel/Apple Silicon
app for the speaker's Mac. AirPlay mirrors the audience app/window; the remote
controls the same live slide through two buttons. It does not stream slides or
relay microphone audio. Both computers join the configured Meet directly.

On a trusted home network, pair once using the host's six-digit, single-use,
five-minute code. Bonjour discovers the source. The remote remembers its exact
TLS certificate fingerprint and bearer token in Keychain; the source persists
only the token hash. Subsequent requests require the remembered certificate and
token. Initial pairing is trust on first use, not protection against a hostile
LAN. Duplicate command IDs cannot advance twice; a command bound to a previous
live page is rejected. Only the host can start/end the worship workflow.

The host remembers the private Meet URL in its local `service-settings.json`.
It is absent from source, installers and the website's public folder API.
**準備主日** checks the folder, starts pairing and asks a Swift helper to join
Meet muted and share the exact OUTPUT window. **開始崇拜** requires prepared
slides and both computers' verified status, confirms recording before asking
the speaker to unmute. **結束** asks the speaker to mute, stops recording and
leaves. The source microphone remains off and its headphone output is retained;
the speaker app mutes its own output and restores it on normal cleanup.

Every Sunday at **10:00 America/Chicago (Dallas)**, the host invokes the same
guarded start workflow automatically. Open the app and finish preparation before
ten. While running, it requests macOS to prevent idle display/system sleep; this
does not launch a closed app, wake a powered-off Mac, or override manual locking.
The local handled-date reservation is saved before starting, so restart, a
failed recording attempt or a clock rollback cannot start twice. If preparation
is incomplete at ten, or the app misses that minute, the controller reports the
problem and requires a manual start. **取消本次定時開始** cancels the coming
Sunday's trigger without disabling the operator's manual start button.

The helper uses macOS Accessibility and semantic Chrome controls. Grant
Accessibility to **Sunday Presenter** on the host, **Sunday Remote** on the
speaker. Google sign-in and system permissions can still require manual action.
The package binds the renamed Electron executable to its bundle identity;
otherwise macOS can grant the bundle while rejecting its helper's requests.
Prototype signature changes can require removing an obsolete permission entry
and adding the installed app again; toggling that stale entry is insufficient.

Real Meet testing has verified muted join, automatic OUTPUT selection/sharing,
the app's short recording start/stop check and leaving only the host. Initial
share confirmation was premature; polling replaces that fixed delay, with the
updated polling build awaiting another real check. Two-computer audio/control
and the full timed service transition remain under acceptance testing.

## Start from source

Requires Node 22.12 or newer and internet access.

```sh
cd tools/sunday-presenter
npm ci
npm start
```

The app opens a black, borderless **full-screen OUTPUT** window. Press
**⌘P (Command+P)** to show the controls. Press it again to hide both the controls
and any private preparation view, returning focus to the slides. This shortcut
works while another application has focus, and is registered only while Sunday
Presenter is running (so it replaces the usual Print shortcut during that time).

The app uses three separate windows:

- **控制台**: operator controls, deck links, and all open pages.
- **PRIVATE PREPARATION**: sign-in, file selection, editor, and new popups.
- **OUTPUT**: black until an explicitly designated audience page is selected.
  Share only this window in Google Meet, and keep it open and unminimized.

Click **Microsoft 登入 / 選檔** to open PowerPoint web privately. Sign in **inside
this app**; Chrome's Microsoft session is separate. Alternatively paste an existing
OneDrive, SharePoint, or PowerPoint HTTPS link in a deck's input. A OneDrive
folder is useful for finding a file; it is not itself a slideshow.

The **本主日簡報** card displays the date returned by the website, calculated in
America/Chicago (Sunday itself is included). Open the dated folder for either
deck. Startup and each explicit folder-open fetch a fresh link; an offline or
stale-date reply clears the link and shows a retry message. Refreshing never
reloads or switches an existing presentation. The app reads the API only; missing
date directories are created when the website generates or uploads a PPT.

1. Open the worship file under **主日崇拜**. Enter PowerPoint's slide show mode.
2. A popup, if any, appears as a new row in the corresponding deck. It stays
   hidden and muted without stealing focus or changing the chosen deck. Click
   **準備 / 檢查** on the desired new row to inspect it privately. All pages stay open.
3. Check that this page contains the audience slides, without notes, editor
   chrome, or presenter controls. On that row click **設為觀眾畫面**. The private
   view returns to the output host but remains hidden until taken live.
4. Prepare **講道** the same way. Confirm its audience page as well.
5. Click **切換到主日崇拜** or **切換到講道**. Each click shows the already loaded
   page and mutes all other pages. **黑屏 / 靜音** hides the audience view and mutes
   everything. Both switch buttons stay pinned at the top while scrolling.
   ⌘1 / ⌘2 switch and ⌘0 blacks while the app is focused. **⌘P** toggles the
   controls globally. Previous/next sends Left/Right to the active page and focuses the
   output window; keyboard interpretation belongs to PowerPoint.

Reopening **準備 / 檢查** revokes that page's audience designation. Inspecting the
currently live page first blacks the output. Full document navigation, renderer
failure, Escape in a live page, or leaving HTML fullscreen also revokes readiness
and blacks the output when that page is live. Close unused rows when approaching
the limit of eight pages per deck.

The output window cannot be closed accidentally; use **⌘Q** while the app is
focused to quit. Closing the controller hides it. The audience window enters
native full-screen once at startup and keeps that window throughout switching;
webpage HTML fullscreen cannot resize it. Output and preparation use the same viewport
size to avoid layout changes during transfer. PowerPoint controls the original
slide's layout and aspect ratio; the app performs no cropping or conversion.

## What is retained

Each page is a sandboxed WebContentsView. Switching changes its visibility and
audio mute state; it does not navigate, reload, recreate, or send slide keys.
Preparation moves the same view between native hosts. Popup handling adopts
Electron's supplied WebContents, preserving opener relationships and POST-based
navigation rather than replaying a popup URL in an editor.

Both decks use `persist:office`. Account cookies and web storage live locally in
`~/Library/Application Support/Sunday Presenter/Partitions/office`. The app does
not export them, inspect credentials, or write deck URLs to a configuration or
log. Sign out through Microsoft's UI when needed. User accounts and remembered
sessions are not part of the source tree or packaged application.

Remote pages have no Node integration, preload, or controller IPC bridge. The
controller accepts IPC only from its own local top-level frame. Navigation is
HTTPS (plus blank popup pages); external application protocols and downloads
are blocked. Only HTML fullscreen for the previewed/live page is permitted;
camera, microphone, screen capture, location, notifications, and other requested
permissions are denied. Meet runs separately in the user's browser. Native
fullscreen/resizing caused by a webpage is disabled to retain the shared window.

## Limits to validate

- Keeping a page alive preserves its in-memory state, **not a paused clock**.
  Background videos, timed animations, and slide auto-advance may keep running,
  even though audio is muted. The prototype does not inject pause/resume code
  into Office. If resuming a timed sequence at the exact instant of switching is
  essential, the architecture has not met that requirement.
- Readiness is the operator's visual designation, not Office UI recognition.
  Same-document UI changes can show editor controls or a sign-in overlay without
  a navigation event. Use black immediately if this happens. Full-document
  navigation and fullscreen exit are guarded, but that is not a guarantee against
  every possible Office UI change.
- Account policies may reject embedded browsers; successful display of the
  sign-in form does not prove authentication. No user-agent spoofing, cookie
  transfer from Chrome, or security bypass is implemented.
- Microsoft lists [animation triggers as unsupported in PowerPoint web](https://support.microsoft.com/en-us/powerpoint/compare-powerpoint-features-on-different-platforms).
  The same comparison lists other web limitations; test the speaker's actual
  file. This prototype cannot promise desktop PowerPoint compatibility.
- Google documents [window/desktop audio sharing requirements](https://support.google.com/meet/answer/16581830),
  including macOS 14.2 or newer. Muting WebContents is not evidence that Meet
  transmits the intended audio; verify with a separate receiving device.
- OneDrive upload uses the host Mac's existing sync client. Filesystem success
  is not proof of cloud availability; the API reports cloud status unverified.
  Agent file intake, code signing, notarization, and an updater remain outside
  this prototype.

## Verification and packaging

```sh
npm test
npm run smoke
npm run icons
npm run package:mac
```

Quit the regular prototype before the smoke run so two copies do not compete
for native keyboard focus. The smoke run opens an isolated temporary profile and loopback fixture server;
it does not use the Microsoft session. It exercises default hidden controls,
native fullscreen, shortcut registration, stable buttons, real Electron views,
popups/opener/POST, shared cookies, keys and slide-step state, HTML fullscreen, mute
flags, resize, redirect, and a renderer crash. Results and synthetic screenshots
are written to ignored `output/smoke/`. It does not test Microsoft or Meet.

Packaging writes an unsigned `Sunday Presenter.app` under ignored `dist/` for
Apple Silicon, plus a drag-to-Applications DMG and ZIP. It omits tests, screenshots, node_modules used
only for development, and account data. Do not distribute it as an accepted
Sunday production app before completing the account/deck/Meet checks.

Website/storage configuration, migration and API contract:
[integration runbook](../../docs/sunday-presenter.md).

Electron references: [WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view),
[popup creation](https://www.electronjs.org/docs/latest/api/web-contents#contentssetwindowopenhandlerhandler),
[web preferences](https://www.electronjs.org/docs/latest/api/structures/web-preferences).
