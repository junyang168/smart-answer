# Sunday Presenter — Meet-first PPT and Remote pilot (#643)

Phase 1 provides two persistent PowerPoint web presentations, one audience OUTPUT
window and a paired local speaker remote. Google Meet is operated manually.
Phase 2 (#643) rolls out in three steps:

1. This Sunday, use Presenter to switch PPT/OUTPUT; both host and speaker laptops
   remain in Meet with the existing service/audio workflow. A separate device
   observes the Remote video as a side test without issuing slide commands.
2. After that is stable, the speaker uses Remote and the existing wireless mic;
   its receiver feeds Meet on the host. Only the host laptop joins Meet.
3. Add YouTube after the app and Meet/Remote workflow are stable.

Local recording is optional and starts only on explicit Start Recording; it is
not required to use PPT, Remote or Meet. Presenter microphone permission and a
recording destination are unnecessary for that Meet-only workflow. Meet still
needs its own permission and input selection. YouTube broadcasting is deferred
until the app/Meet path is stable. Remote microphone relay remains deferred.

The [Phase 2 design](../../docs/sunday-presenter-phase2-design.md) covers local
point-to-point PPT video, replacing the AirPlay dependency,
plus optional recording and later YouTube streaming with a reusable stream key and fixed viewing
link. PPT audio capture, Google OAuth and Remote microphone relay are outside this
design; only the selected wireless microphone on the host supplies audio. It is
a design draft, not an implemented capability of the Phase 1 release below.

## iPad-first Remote pilot (#652)

The owner identified iPad as the actual speaker-device goal; Mac use is a
constraint of the current service workflow. [The iPad target](ipad/README.md)
adds a UIKit viewer/controller using the same bundled WebRTC receiver and pinned
Presenter protocol. It defaults to observe-only mode. Pairing, host isolation,
touch navigation, expanded viewing and foreground reconnect are implemented as
a pilot. Xcode simulator and unsigned device builds now pass. An iPad simulator
has received real PPT video, controlled both slide directions, displayed the
expanded landscape viewer, and restored video/pairing after app restart and
foreground return. Physical-device signing and hardware/full-service acceptance
remain outstanding. Keep the first Sunday's two-laptop Meet workflow.

## Local video pilot — 0.5.1 (#643)

The worktree pilot adds a resizable PPT viewer to the native Remote. When paired
with this Presenter, it receives only the current OUTPUT over local WebRTC and
keeps the existing slide buttons and arrow keys. Remote requests no microphone
or camera permission. The host allows capture of the exact OUTPUT window only;
preparation, source loss and minimization stop the viewer instead of selecting
another window. Pairing and slide control continue using certificate-pinned TLS.

0.5.1 fixes slide keys in embedded PowerPoint pages, accepts arrow keys when the
Remote video viewer has focus, and keeps command errors visible across state
polls. The host restores the confirmed player frame and uses Chromium's input
path for embedded frames; it does not click the slide or send global OS keys.

Remote 0.5.2 fixes switching from a remembered Presenter to another Mac. Only
the selected host is resolved; remembered credentials are reused only for their
exact certificate identity. Selecting another host requests that host's six-digit
code, and late replies from the previous connection cannot change the new one.
The host selector remains available after connecting. This Remote-only update
works with Presenter 0.5.1; only the most recently paired host is remembered.

Remote 0.5.3 places the PPT above a compact bottom control area, removes the
large heading, and adds a Full Screen button plus Control-Command-F. The window
now uses macOS primary full-screen behavior. Playback keeps the Mac awake only
while a live presentation is visible, and the heartbeat runs in common run-loop
modes. A still slide no longer triggers reconnect merely because decoded frames
stop increasing; brief ICE disconnections get five seconds to recover, while
persistent failure and first-frame timeout still reconnect automatically.

## Local MP4 recording — 0.6.1 candidate (#643)

The controller selects an explicit host microphone and save location, starts
recording, displays its input meter, and stops/finalizes an MP4. OUTPUT is captured
once and shared with Remote. The recording branch samples that same track through
a 1280×720 canvas at 30 fps so an unchanged slide has continuous video timing.
The selected microphone passes through a 48 kHz Web Audio destination before
encoding, keeping a continuous audio render clock. Direct USB-track recording
showed a 1.84-second audio/video duration difference in a 150-second physical
AudioPro test; the normalized path reduced that difference to about 28 ms.
A 90-minute installed-app run saved 90:17 of actual PPT + AudioPro to SSD,
fully decoded without errors, with a 25 ms A/V duration difference. Video,
writing and finalization endurance passed; two ~0.13-second decoded audio
silences remain unattributed, so uninterrupted-sound acceptance is pending.
See `acceptance.md` for the measured limits. Capture/encoder state is retained on failure without
logging document URLs or audio samples.

MediaRecorder uses H.264/AAC, 500 ms keyframes/timeslices, incremental bounded IPC
and file writes. It never captures PPT/system audio or routes microphone audio
to the speakers. Remote still receives video only and needs no microphone access.

Choose **選擇／更新麥克風**, select the wireless receiver, then start and choose
an MP4 path (including an SSD). Refreshing devices requests OS permission but
opens no microphone track. Start uses exact deviceId, without fallback to another
input. Source/preparation/minimize loss or microphone disconnection automatically
stops and finalizes the current clip. Encoding/storage failure retains a named
partial file, reports failure, and never overwrites a completed recording.
Queues are limited to two seconds or 16 MiB; missing encoder output also stops
recording. Quit waits for finalization and releases microphone/capture resources.

`npm run smoke:recording` records actual OUTPUT plus generated reference audio,
checks that MP4 packets are readable before stop, independently decodes both
tracks with ffprobe/ffmpeg, and compares a PPT color change with a tone change.
It also checks Remote/recording capture ownership, source privacy stop and mic
loss. Add `-- --recording-long-smoke` for an additional untouched 120 seconds.
`electron . --smoke --focus-smoke --recording-hardware-smoke` explicitly selects
the actual AudioPro X5 input, records ten seconds and inspects decoded AAC.
These verification tools are not required by the packaged recording feature.

This remains a local pilot. Fixed-key YouTube streaming, scheduling, standalone
microphone test/mute and clean-audio full-service acceptance remain unimplemented
or pending. Website downloads remain the previously released 0.4.0. Remote 0.5.3
is compatible with this host; only Presenter needs updating for local recording.

Run the pilot from this worktree with `npm ci` and `npm start` in this directory;
quit the installed Presenter first. Build the Remote with `node build-native.cjs`,
then open `output/native/Sunday Remote.app`. Use the existing six-digit pairing
flow. Presenter may need macOS Screen Recording permission; Remote needs local
network access. No Accessibility, microphone or camera permission is needed for
this video milestone. Full screen is available through Remote's window controls.

After an ad-hoc pilot update, macOS can retain the old signing requirement even
when Screen Recording shows On. If capture remains denied, remove only the old
Presenter entry in System Settings, add `/Applications/Sunday Presenter.app`
again, and restart Presenter. Complete any Keychain prompt yourself to retain the
existing Remote pair. The 0.5.1 installed-app check passed actual Office animation,
next/previous controls and live video after that renewal; no PPT content changed.

`npm run smoke:video` builds both Remote architectures and runs the real Electron
OUTPUT → native WKWebView loop on one unlocked Mac. It uses disposable pairing
and a separate host profile, never saved user accounts. Evidence is written to
`output/smoke/video-result.json` and `output/smoke/remote-video.png`. This confirms
local engine integration, not a second physical Mac, church hotspot, real Office
files, minimum-OS support or a full service's duration. See [acceptance](acceptance.md).

## Install and use — released Phase 1

Download v0.4.0 from church administration → Sunday service → Download Sunday
Apps. Install Sunday Presenter on an Apple Silicon Mac (macOS 13+) and Sunday
Remote on the speaker Mac (universal Intel/Apple Silicon). No Node.js installation
is needed. The package is ad-hoc signed, without Developer ID/notarization; use
macOS Privacy & Security → Open Anyway after verifying the downloaded source.

1. Open Presenter. A movable settings window appears; no blank OUTPUT appears at
   startup. Open this week's PPT and sign into Microsoft inside Presenter.
2. The church API supplies the upcoming Sunday folder in America/Chicago time.
   The unique worship-named file and sole remaining sermon PPT are assigned to
   two fixed cards. Ambiguous choices require selection. In each PowerPoint file,
   start the slideshow, inspect it and press Confirm and Play.
3. Playback hides settings. Command-1 selects worship, Command-2 selects sermon;
   arrow keys flip the current slideshow. Switching retains the same views and
   slide state. Dock activation or Command-P restores settings; closing settings
   leaves playback running. Command-Q exits.
4. Enable speaker slide control. On the same trusted local network, open Sunday
   Remote, enter the six-digit code once and allow local-network access. Pairing
   is remembered; discovery and reconnection are automatic. Either operator or
   speaker controls the same live slideshow. AirPlay mirrors OUTPUT; HDMI feeds TV.
5. Manually open and join Meet in Chrome. Share **Sunday Presenter — OUTPUT** once
   and keep that window open and unminimized. The host microphone remains manually
   muted; use earphones to monitor. On the speaker laptop, manually configure Meet
   microphone and mute speaker output to avoid echo. Manually start/stop recording
   when available and leave Meet at the end. Confirm actual audio/video from an
   independent receiving device before the service.

Presenter and Remote do not request Accessibility or microphone access, launch
Chrome, join/leave Meet, change system sound, control recording or schedule a
10:00 start. Old service-settings.json files are ignored. Exit/update both apps
when moving from the earlier automation prototype. Remote is slide-only in Phase 1.
The running Presenter prevents idle display sleep; it does not override manual
locking or start itself after being quit.

## Architecture and boundaries

Each deck is a sandboxed persistent WebContentsView. Private sign-in/preparation
and the single OUTPUT share a viewport; only an explicitly confirmed audience page
can play. Popup adoption preserves opener and POST navigation. Switching does not
reload the page. Full-document navigation, fullscreen exit and renderer failure
revoke readiness, mute and hide the invalid audience page. There is no manual
blackout window/button. On macOS OUTPUT covers display bounds without entering a
separate fullscreen Space, so it stays available to the Meet window picker.

Both decks use persist:office. Microsoft sessions remain local under Application
Support/Sunday Presenter/Partitions/office. No cookies, credentials, private Meet
URLs or pairing identities are packaged. Office views cannot access controller
IPC, Node.js or OS capture/microphone permissions. Controller IPC accepts only its
own local top-level frame.

Remote uses Bonjour, certificate-pinned TLS and a remembered bearer token in
Keychain. The host stores its hash. Initial pairing assumes a trusted LAN; codes
are single-use, expire after five minutes and are rate-limited. Only paired arrow
commands for the current audience binding are accepted; duplicates are applied
once and stale commands are rejected. No Meet state, workflow or microphone
commands are exposed by the Phase 1 protocol. Audio does not share this channel.

The church's iPhone hotspot must allow peer connectivity between both Macs.
Flip commands stay local; Office and manually operated Meet use the internet.
Keeping a page loaded preserves state, not paused timers: background video,
animations and automatic slide advances may continue. Readiness is the operator's
visual confirmation. Office account policies, unsupported web animations and
actual Meet audio require testing with the intended files and receiving device.

## Development and packaging

Requires Node 22.12+:

```sh
cd tools/sunday-presenter
npm ci
npm test
npm run smoke
npm run package:mac
```

Quit the installed Presenter before smoke testing to avoid competing shortcuts.
Smoke uses an isolated temporary profile and loopback fixture. It checks one
OUTPUT, retained switching/keys, popup POST/opener, focus, hidden settings, capture
window bounds, remote commands, and rejection of removed Meet actions. It does not
sign into Microsoft or operate Meet. Native Remote builds for arm64 and x86_64;
DMG and ZIP outputs live in ignored dist/. Test/account data is excluded.

Published installers are immutable files outside Git. scripts/publish-sunday-presenter.cjs
uses --status released for Phase 1; the website accepts released and older pilot
manifests. Build verification and real user observations are in acceptance.md.
Website/storage API: ../../docs/sunday-presenter.md.

## Shared release version and Mac/iPad Remote behavior

Presenter, Mac Remote and iPad Remote use the same release version from
`package.json`. Run `ipad/generate-project.cjs` after a version change; both Xcode
configurations obtain `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` from that
value. Do not manually version the generated project separately.

Both Remote apps start in observer mode. Disable **只看畫面** explicitly to enable
slide buttons and keyboard navigation. Pairings are stored separately for each
pinned Presenter identity; **重新配對** removes only the selected host. Mac upgrades
can read the previous single-host Keychain item only for its exact fingerprint.
Both clients use the same endpoint/certificate checks and reject HTTP redirects.
