# Sunday Presenter — Phase 1 (#598)

Phase 1 provides two persistent PowerPoint web presentations, one audience OUTPUT
window and a paired local speaker remote. Google Meet is operated manually.
YouTube streaming, speaker microphone relay and automated broadcasting belong to
Phase 2 (#643).

The [Phase 2 design](../../docs/sunday-presenter-phase2-design.md) covers local
point-to-point PPT video and speaker audio, replacing the AirPlay dependency,
plus YouTube streaming and recording. It is a design draft, not an implemented
capability of the Phase 1 release below.

## Install and use

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
