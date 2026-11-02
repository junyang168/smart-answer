# #590 acceptance evidence

This record distinguishes a working local harness from Sunday-service
acceptance. No Agent integration or production deployment has been performed.

## Local technical checks — 2026-11-02

Environment: macOS 26.5.2, Apple Silicon, Electron 45.2.1 / Chromium
156.0.8078.75. Run from this card's dedicated worktree.

| Check | Observed result |
| --- | --- |
| Switching invariants | 50 mocked switches; exactly one visible audience view and all inactive pages muted |
| Real Electron state retention | 20 switches; each fixture retained its original random page token and reached exactly 10 slide/animation steps |
| Output identity | Same native output window ID across the switches and HTML fullscreen |
| Popup handling | Real `window.open` adopted into a hidden, muted view; `window.opener` retained; current live page, preview and chosen deck unchanged; explicit preparation required |
| Full-screen output / controls | Native borderless full-screen at startup; controls hidden by default; ⌘P registered globally and toggle callback shows/hides controls |
| HTML fullscreen | Fixture entered/left document fullscreen within the existing native full-screen output; exit revoked readiness and blacked output |
| Session sharing | Two real views shared a session and a fixture cookie |
| Isolation | Remote fixture had no controller bridge, Node integration disabled, sandbox enabled |
| Failure behavior | Full document redirect and forced renderer crash revoked readiness, muted audio and blacked output |
| Viewport | Output resize propagated to parked/preparation views |
| Popup POST | Actual form submission to a popup retained the exact POST body |
| Control stability | Title/state updates retained the same row buttons |
| Presentation shortcut | App-directed Command-P intercepted before presentation page handlers; controls can be shown over preparation, then hidden together |
| URL rules | HTTPS/blank allowed; file, JavaScript, data, insecure HTTP and external app schemes rejected; loopback allowed only for exact smoke origin |
| Package dependency audit | `npm audit`: zero vulnerabilities after pinned glob/minimatch overrides |
| Existing website TypeScript | Passed after restoring this worktree's declared web dependencies with `npm ci --ignore-scripts` |

Generated proof: `output/smoke/result.json`, `control.png`, and `slide.png` (ignored,
synthetic content only). These prove local runtime behavior, not Meet reception,
real Office rendering, actual sound output, or a Microsoft login.

## Account, real-deck and Meet checks — pending

The owner completed Microsoft sign-in inside PRIVATE PREPARATION. The supplied
OneDrive folder displayed both actual PPT files. After quitting and relaunching
the prototype, PowerPoint web greeted the signed-in user and the same OneDrive
folder opened without another login. The worship PPT opened in PowerPoint web
with its 22-slide list and entered a clean slideshow in the same native output
window. An auxiliary Office OAuth popup returned a first-party consent error;
it was isolated from the audience page, which continued to work. The prototype
was updated so such popups cannot steal focus or change the selected deck.
No browser security bypass, user-agent spoofing, or cookie
transfer was used. No private URL or account identifier is stored in this record.

Record the following before selecting this architecture or closing #590:

| Check | Evidence needed | Status |
| --- | --- | --- |
| Embedded Microsoft sign-in | Owner completes login; signed-in OneDrive/file opens inside this app | Passed for owner's personal Microsoft account |
| Persistence | Quit/relaunch with saved Microsoft session | Passed for portal, folder and both files |
| Two real PPTs | Worship and animated sermon open with original layouts/aspect ratios | Passed locally; sermon retained its narrower aspect ratio with side bars |
| Office slideshow/popup | Both actual audience pages enter slide show while output window identity stays fixed | Passed for both files in windowed baseline |
| Repeated switching | Worship → sermon → worship; note actual slide and animation state before/after | Windowed baseline retained worship slide 1 and sermon slide 2 on return; broader animation coverage pending |
| Notes/control exclusion | Independent receiving client sees slides only during preparation, switching, and slide show exit | Received slides without operator controls during baseline switching; full-screen overlay/exit checks pending |
| Meet video | Continuous window share on an actual Meet with a separate receiving client | Passed in windowed baseline using a second signed-in Chrome window on the same Mac/account; full-screen revision still to verify |
| Meet audio | Active audio/video heard remotely; inactive deck inaudible, including during switches/black | Pending |
| Timed media | Document whether background auto-advance/media continues and whether that meets the operator's need | Pending |
| Unsupported features | Test actual sermon effects, triggers, fonts, videos, links and transitions; list deviations | Pending |

Do not mark the local synthetic tests as any of the pending account/deck/Meet
results. If Microsoft blocks embedded authentication or native-host switching
breaks Office presentation state, record that failure before pursuing broader
integration.

The baseline Meet used a newly created private test room. An anonymous
incognito receiver was denied entry; no meeting access policy was changed.
A second signed-in client joined with camera and microphone off, received the
output, and showed the sermon slide after switching without a new share. Both
clients left and the share was stopped. The capture picker offered system audio
and it was selected, but that does **not** prove audible delivery or audio
isolation. No church group messages or invitations were sent.

The owner then requested full-screen output and controls hidden until a two-key
shortcut. The final binding is **⌘P (Command+P)**. The full-screen revision uses a
BrowserWindow with persistent child WebContentsViews. Native fullscreen is set
once at startup; a temporary simple-fullscreen experiment could not reliably
obtain native keyboard focus and was removed. The full regression smoke passed
with native fullscreen, including native key delivery across 20 switches.

The packaged app launched directly into borderless native full-screen with a
black output and no controller. Native CUA **⌘P** showed the controller; a second
**⌘P** hid it and returned to output. OS-global shortcut registration and an
app-menu accelerator are both present; the app-menu accelerator also supports
app-directed synthetic key events. The full-screen real-deck/Meet repetition
was temporarily interrupted by the Mac locking, then resumed after manual unlock.
The worship file was subsequently observed filling the packaged output with no
title bar or controls. Both files again opened with the saved Microsoft session.
An app-directed shortcut edge case inside Office was fixed by intercepting
Command-P on each remote WebContents before the page handles it; the full smoke
suite passed with this counterexample and the preparation/controls toggle cases.

Installed at `~/Applications/Sunday Presenter.app`. All seven runtime files in
the installed ASAR were compared byte-for-byte with the worktree and matched.
The installed app's actual worship slideshow accepted Command-P and showed the
controls; the sermon was also loaded and designated ready. Both remain loaded.

The full-screen Meet re-test rejoined the same private room with camera and
microphone off. Meet returned “Unable to share your screen / An error occurred
while sharing your screen. Please try again” before a new picker appeared.
Automatic Dock activation timed out; manual foreground activation / picker
verification was requested. This is **not** a full-screen Meet acceptance pass.
The earlier second-client windowed reception remains valid but does not prove
the new full-screen capture path. Full-screen capture, control-overlay exclusion,
audible delivery and broader animation coverage remain pending; keep #590 open.
