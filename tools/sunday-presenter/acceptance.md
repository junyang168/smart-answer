# #590 acceptance evidence

This record distinguishes a working local harness from Sunday-service
acceptance. No Agent integration or production deployment has been performed.

## #598 remote, Meet automation and scheduled start — 2026-11-02

The following is newer than the historical #590 notes below. Website integration
was subsequently deployed under #590/#597. This feature does not redeploy it.

| Check | Evidence / scope |
| --- | --- |
| Local protocol and workflow | Node tests cover TLS pairing/restart, token hashing, exact certificate pinning, command deduplication, stale-page rejection, host-only workflow authority, recording verification, cleanup and cancellation |
| Fullscreen / shared source | Real Electron smoke passes 20 retained-state switches plus remote and operator commands applied to the same live page, duplicate IDs applied once |
| Native builds | Swift helper and remote compile for arm64 and x86_64; universal bundle and installed host signature verification pass |
| Website | Installed controller reads the actual production upcoming-Sunday folder |
| Permission recovery | Initial Electron executable retained its upstream identity. TCC granted the bundle ID but checked the child against an executable path. Ad-hoc signing the complete host bundle fixes that mismatch. An obsolete permission must be removed and the installed app re-added; toggling retained a stale code hash. The owner performed authorization through macOS UI; no TCC database was modified or security protection disabled |
| Real Meet join | App automatically joined the configured room with microphone and camera off, verified in Meet's actual controls |
| Real Meet output share | App selected OUTPUT and pressed Share without manual picker selection. Meet displayed its presentation. A 0.5-second confirmation was too early and reported failure; operator confirmation then verified sharing. The source replaces that delay with bounded polling; that final adjustment still needs a real rerun |
| Recording entitlement | One manual short recording started and stopped, with Gemini notes and transcription disabled |
| Automated recording | The app's first-setup short recording check automatically started recording, verified it, and stopped it. Actual Meet returned to Start recording, with microphone/camera off and extra notes/transcription disabled |
| Automated exit | App's End selected only the local host's leave action, handled the host's extra dialog, and Meet displayed the left-meeting page |
| Evidence | Private account/room screenshots under `~/.local/state/sunday-presenter/ops-598/`; not packaged or committed |
| Sunday 10:00 | Deterministic clock tests cover Dallas standard/daylight time, the DST transition Sunday, before-ten/no-weekday starts, cancellation, restart deduplication, unready state, missed time and durable-reservation failure. These are not an actual two-computer service or a real Sunday-clock acceptance run |

Pending: two physical Macs, remembered discovery on the church LAN, AirPlay plus
remote slide controls, speaker audio received independently through Meet,
remote disconnection mute, and the complete scheduled recording/microphone
transition. No speaker microphone was opened in this one-Mac test. Two short
test recordings were created in the owner's Meet; cloud processing/file playback
has not yet been verified. No invitations or church messages were sent.

Prototype packages are ad-hoc signed, without Developer ID or notarization.
Updating this prototype can change the TCC code hash and require reauthorization;
the one-time home-setup goal across upgrades is not established by this build.

Additional real-file/native-client checks:

- The owner's newly supplied test PPT opened from the actual 11-8-2026 cloud
  folder inside the installed Presenter, with 29 slides and animated steps. The
  generated worship PPT also opened there, with 28 slides. Fullscreen output
  showed the slides without editor/controller UI. Keyboard and controller Next
  were exercised. Switching away and back retained sermon slide 3 and worship
  slide 2; this is a sample, not blanket acceptance of every Office animation.
- Native Remote testing found the original Bonjour protocol label exceeded
  Apple's 15-character limit. Version 0.3.1 centralizes a shorter service type
  for host, client and bundle declarations. In-place build-directory launch also
  retained stale Info.plist registration; the installed app discovered correctly.
- An installed native Remote paired over actual TLS with an isolated synthetic
  source and delivered both button directions. Client restart reused its
  Keychain pairing. Source restart on a new port reconnected automatically and
  accepted another command. Host revocation disabled both controls and cleared
  the remembered synthetic pairing. The fixture and remote GUI were stopped;
  no microphone, output-volume or Meet action was part of this fixture test.
- The owner identified iPhone 5G Personal Hotspot as the actual church network.
  The two-Mac, hotspot, AirPlay and Meet-audio checks remain pending. Home Wi-Fi
  or same-Mac fixture checks do not establish that acceptance.

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


## Paired remote and website pilot distribution — 2026-11-02

The 0.3.1 pilot is available from authenticated Sunday-service administration
through the separately merged/deployed #613 / PR #617. Production release
`e8d1561c` passed the canonical deployment and health checks. The real signed-in
production page displays both installer links and the upcoming 11/8 folder.
Anonymous installer metadata/files redirect to sign-in, while the exact upcoming
folder endpoint remains 200/no-store and adjacent administration stays protected.
Published installer hashes match the staged bytes. Both installers previously
passed development-browser download/readback. Actual production Chrome download
attempts returned `ERR_BLOCKED_BY_CLIENT`. The owner subsequently clicked the
production Sunday Remote download and confirmed twice that it downloads. Record
this as owner-confirmed production Remote download success; production browser
hash readback and the Presenter DMG download were not independently verified.

The main Mac was updated from 0.3.0 to the published 0.3.1 bundle, retaining a
backup and its user-data directory. Installed ASAR bytes match the staged bundle,
the deep strict signature verifies, and the app starts with the actual upcoming
folder and Sunday 10:00 Dallas schedule visible. Installed Sunday Remote
successfully discovered this actual host via Bonjour. No same-Mac speaker Meet
or microphone run was started. The discovery test service was stopped and
Remote exited; Presenter remains idle with black/muted output.

The short Bonjour service type and client endpoint re-resolution fixes are
covered by 27 passing app tests. Eight website authorization/download/publisher
checks and TypeScript pass. Previous actual PPT advance/switch-retention checks
and same-Mac native TLS fixture checks remain partial evidence only. Physical
two-Mac control over the owner's iPhone hotspot, AirPlay with HDMI, speaker audio,
disconnection mute and the complete timed service transition still require
acceptance. Keep #598 / PR #611 in draft. Ad-hoc updates may invalidate macOS
Accessibility grants; no renewed grant was assumed or changed during this update.


## Playback UX correction — 0.3.2, 2026-11-02

Owner use exposed the hidden popup/page inventory as an unacceptable primary
workflow. Startup now shows the playback controller, with two fixed deck cards;
internal page records and manual links are collapsed. The current inspected
slide has one confirm-and-play action, exercised through the actual controller
in the Electron smoke. Confirmed deck cards remain stable despite stale popup
records. The existing manual inspection requirement is retained; this does not
automatically validate editor content as an audience slide.

A single weekly-folder action reads visible file names inside the owner's
existing OneDrive session. A unique worship-named PPT plus exactly one other PPT
are assigned to worship/sermon and opened through that folder. Ambiguous file
sets require selection. Folder identity survives OneDrive's date URL encoding;
polling is bounded to two minutes and ends after opening both files. Repeated
document GET opens reuse their private views; POST popups retain their original
body and opener. Only a document opened from the current private preview may
automatically follow into it; authentication popups do not steal the audience.

Actual signed-in OneDrive test: one button opened the 11/8 generated worship PPT
and the owner's test sermon, and the real controller showed each file in the
correct card. These were real Office editors, not fixture page titles. Both real PPTs were then put into Office slideshow mode, confirmed and played
through the new primary buttons. Switching back to worship and a controller
Next action produced the actual second slide. The PowerPoint slideshow command
still needs operator action and visual confirmation.
Thirty-one Node tests pass, and the full Electron smoke passes, including UI
confirm-and-play, 20 switches, key delivery, POST preservation and blackout gates.
No new Meet, two-Mac audio, or hotspot acceptance is implied by this UX change.


## Downloaded-install first launch — 2026-11-02

Owner installation on this Mac revealed two application locations: system
Applications contained verified 0.3.2, while the running process was the older
0.3.1 in the user's Applications directory. The old process was quit normally,
and its bundle moved to a disabled backup outside Applications. User-data and
Office sessions were retained. The downloaded 0.3.2 was copied from its mounted
DMG into system Applications using Finder.

Its initial launch did not reach application code: the process remained at
`_dyld_start` with a 96 KB footprint, and CoreServicesUIAgent displayed the
Apple-cannot-verify alert. Privacy & Security showed the matching Open Anyway
entry. This reproduces the owner's downloaded-package Gatekeeper friction;
local source/bundle functional checks do not complete that first-launch test.
The owner was asked to perform the security exception and authentication
personally. No quarantine removal, protection disablement or automated security
exception was applied. New downloaded-install UI acceptance remains pending that
manual confirmation. Developer ID signing/notarization is still absent.
