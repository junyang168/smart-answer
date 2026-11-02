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

## Account, real-deck and Meet checks

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
| Meet video | Continuous window share on an actual Meet with a separate receiving client | Windowed baseline observed on a second signed-in Chrome window; after rejoining and restoring the full-screen share, owner reported “work like a charm!”; see scope below |
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
microphone off. Automation initially received “Unable to share your screen /
An error occurred while sharing your screen. Please try again” before a picker
appeared. The owner then manually shared the app window successfully.

A second signed-in Chrome window joined and initially received the complete
borderless worship slide. The operator control window was shown and was absent
from the received picture, but the same-Mac receiver subsequently showed partial
slide painting and then a blank presentation region during switching. The local
app output still displayed the complete sermon slide. Meet's receiver popout
also appeared blank. This does not distinguish a transmission problem from
background rendering in the same-Mac/Space test setup; it is **not** a stable
full-screen reception acceptance pass. A receiving phone/other computer was
requested to resolve that uncertainty.

During the owner's subsequent test, a blank-screen report was initially treated
as a capture failure. A fresh check instead found both Chrome windows at the
Meet home page: there was no active call/share at that point. That report is
therefore **not evidence of a full-screen capture defect**. The test room was
rejoined with microphone and camera off, and Meet again displayed “You're
presenting” with a slide visible in its presentation preview. The owner then
reported **“work like a charm!”**. Record this as a successful owner-reported
live sharing check after restoring the meeting/share, not as a code fix.

The final source-picker selection, receiving-device identity and an exact
repeated-switch/animation sequence were not independently recorded in that
last exchange. The earlier same-Mac rendering anomaly remains unresolved; it
must not be conflated with the later inactive-share incident. Audible delivery,
timed-media behavior and broader animation coverage remain pending. Keep #590
open and the PR in draft until the remaining acceptance evidence is complete.

## Website / OneDrive integration and portable package — 2026-11-02

The owner extended the scope to website integration and confirmed the existing
OneDrive Personal Sunday sync root and unpadded month-day-year date directories.
Generation and final uploads now create the relevant missing date directory and
save deliverables there. Generated output and human final uploads use separate
names. The assistant publishes only revalidated artifacts, before marking jobs
ready. Final downloads and email attachment resolution use the new location.
Service metadata, templates and immutable assistant artifacts remain internal.

The website exposes a no-store upcoming-Sunday API and a management-page folder
entry. The app fetches it at startup and before opening the folder, validates
the Chicago date and OneDrive destination, and clears stale/failed links.
Reading the API does not create a folder. Website generation/upload creates it
when needed. Refresh failure leaves the live deck unchanged.

Validation: 59 backend tests passed (including missing directories, Sunday/DST/
year boundaries, direct website generation, final upload/download/email paths,
superseded input exclusion, copy failure, ready-after-publication and migration
collisions); eight Node tests passed; website TypeScript passed. The real Mac
Electron smoke also passed with website HTTP fetch/render and a failed refresh
while a presentation remained live, alongside its existing 20-switch checks.

Under the owner's requested migration, 103 historical PPTs were copied into 51
dated OneDrive folders. Every original and copy was read back against its SHA;
the originals were retained. Templates/lock files/JSON were excluded. Private
manifests are in ignored `output/sunday-presenter-integration/`. The upcoming
11-8-2026 folder was also created and observed on OneDrive's actual website,
empty as expected. A local write is not proof of cloud completion for every
migrated file; the API does not claim cloud verification.

Version 0.2.0 has a custom vector/native icon, arm64 DMG and ZIP with installation
instructions, bundle ID `org.dallas-hlc.sunday-presenter`, and minimum macOS 13.
DMG checksums and ZIP integrity were verified. Packaging now verifies the actual
app executable and portable internal symlinks; an initial nested-bundle mistake
and a filesystem-copy symlink-resolution defect were caught in installation
checks and fixed before delivery. The final DMG app was installed into
`~/Applications/Sunday Presenter.app`, launched successfully and accepted ⌘P.
Its controller displayed 2026-11-08 / 11-8-2026 from this worktree's actual
website API; its folder button then opened that exact OneDrive folder inside
PRIVATE PREPARATION using the retained Microsoft session. Installers were copied
to `~/Downloads/` with byte-hash comparison.
They contain no Microsoft session or host-specific OneDrive configuration.

The installed app is running against the local website preview for this check.
Distributables default to the production website. Website code/config activation
has **not** been deployed; production activation remains required before another
laptop can obtain automatic links. The package is unsigned and not notarized.
The second laptop itself, audible Meet delivery and broader real animation
coverage have not been tested.
