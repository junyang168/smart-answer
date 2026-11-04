# Sunday Remote for iPad — pilot source (#652)

## Free owner-only automatic renewal pilot (#655)

The owner tested real iPad PPT video/control on Wi-Fi and an iPhone hotspot.
For this single-user pilot, the Mac now has a free Personal Team renewal helper;
no paid membership, third-party signing service, password file or security bypass
is used. **This does not remove Apple's seven-day limit.** It rebuilds a fixed
source snapshot with Xcode, requests a newer profile, verifies its actual expiry
and binding, and installs it over the existing app using official devicectl.
The normal install preserves the app container and Keychain; post-update
reconnection is an independent check, not inferred from installation success.

On 2026-11-04, a real renewal advanced expiry from November 11 at 13:47 to
14:31 Chicago time, and installation succeeded over `localNetwork` while iPad
was passcode-locked after first unlock. No USB or repeated Keychain password
was needed. The actual helper then performed another successful locked wireless
renew/install and advanced expiry to 14:36. These were actual runs, not merely
an unsigned build or reinstallation of a profile with unchanged expiry.

### Operating rules

- A user LaunchAgent checks hourly and at login. If over four days of installed
  validity remain, it does nothing. It retries availability on later checks;
  background builds are limited to one attempt per day. A deliberate manual
  `--force-due` test can retry a build, but cannot bypass lock or Sunday guards.
- All of Sunday in **America/Chicago** is excluded. iPad must be paired,
  reachable and passcode-locked, having been unlocked since its last reboot.
  The lock is checked again immediately before installation. If the user
  unlocks during compilation, installation waits; it never kills or launches
  Sunday Remote. This reduces interruption; the lock check and install are
  separate OS operations, so their boundary is not an atomic foreground lock.
- Mac must be awake, signed in to the configured user, connected to the Internet,
  and able to reach iPad on the same local network. Screen-locking the Mac is
  distinct from logging out; unattended renewal with the Mac screen locked has
  not yet been independently tested. A locked/expired login Keychain, new Apple
  sign-in/2FA requirement or certificate problem may still require owner action.
- Source is a fixed, SHA-checked snapshot. Renewal does **not** pull a branch,
  upgrade application code or replace a live worktree. New features need an
  intentionally updated snapshot and separate validation.
- Only cached profiles for this exact team, bundle and iPad are backed up and
  temporarily removed so Xcode requests a fresh profile. Original cache entries
  are restored afterwards if absent. No certificates or server profiles are
  revoked. Wrong bindings, unchanged/short expiry and failed installations never
  advance the recorded installed expiry. Pending verified artifacts are reused
  for a later installation attempt rather than signing repeatedly.
- Runs hold a process lock, use bounded subprocess timeouts and rotating logs,
  and require at least 3 GiB free at home and 5 GiB on the configured work volume.
  A timeout/auth failure becomes `needs_attention`, not a false renewal pass.

### Local deployment and storage

Private config, state and deployed script are under
`~/Library/Application Support/Sunday Remote Renewal/` (config/state mode 0600).
The LaunchAgent is `~/Library/LaunchAgents/org.dallas-hlc.sunday-remote.renewal.plist`.
No team/device identifiers, credentials or signing artifacts are committed.

The initial SSD-based helper succeeded when run interactively, but a real
LaunchAgent attempt failed with `Operation not permitted` on the removable
volume. That failed attempt is retained as evidence, not counted as background
success. The small dedicated snapshot/build workspace was therefore moved to
the private application-support directory (initial measured build size ~81 MiB).
Generic iOS builds avoid fetching physical-device debugging symbols. The large
DeviceSupport cache remains on SSD. No Full Disk Access grant is requested.

```sh
CONF="$HOME/Library/Application Support/Sunday Remote Renewal/config.json"
TOOL="$HOME/Library/Application Support/Sunday Remote Renewal/renew.py"
/usr/bin/python3 "$TOOL" --config "$CONF" status
# Manual check; default due/lock/Sunday guards still apply:
/usr/bin/python3 "$TOOL" --config "$CONF" run
# Explicit one-off engineering test, retaining lock/Sunday guards:
/usr/bin/python3 "$TOOL" --config "$CONF" run --force-due
# Register/re-register the hourly user agent:
/usr/bin/python3 "$TOOL" --config "$CONF" install-agent
# Disable the schedule (does not uninstall the iPad app):
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.dallas-hlc.sunday-remote.renewal.plist"
```

`state.json` records actual installed expiry separately from the latest check,
build attempt and error. Inspect it after a missed renewal; an enabled schedule
alone is not evidence of success. Run deterministic guard/failure/timeout tests
with `/usr/bin/python3 tools/sunday-presenter/ipad/test_renew.py`.

The original pilot description and historical checkpoints follow.

**Current operational hold (2026-11-04):** both the hourly agent and the one-off
background verifier were unloaded after the owner reported that the iPad could
discover the MacBook but repeatedly requested a six-digit code. The owner then
reported successful connection after pairing again. Initial, renewed and
background-candidate app signatures have the same application/team identifiers;
this comparison does not establish why the old pairing was unavailable or
rejected. The client also selects the first discovered host on foreground return,
which can select the Mac mini instead of MacBook; host selection and pair retention
need separate verification. Do not call this an expiry failure or a proven
Keychain-loss bug. The background verifier had signed a newer candidate but
deferred installation when the iPad was unlocked; it did not install that
candidate. Keep automatic updates disabled until reconnect/pair retention on the
intended MacBook is checked. The existing iPad app remains installed.

iPad is the intended speaker device. The existing Mac app remains available
for the current service; it is not a requirement for the future speaker setup.
This target depends on the unmerged Presenter/video pilot in #643 / PR #644.

The pilot receives the host's audience PPT video, pairs with its six-digit code,
and offers touch previous/next controls. **Only-view mode is on by default**:
the first-Sunday observer cannot issue slide commands until explicitly turning
that mode off. The expanded viewer hides connection details while retaining
navigation and a way back to settings. Pairing secrets are stored per host in
device-only Keychain entries. The exact Bonjour-advertised certificate and its
selected endpoint are pinned; HTTP redirects are refused. A host switch or
background transition cancels the old session and invalidates late callbacks.
Slide commands have IDs and bindings and are never replayed after an uncertain
response. Foreground return starts discovery and reconnects saved host pairs.

The target compiles the **existing** `native/RemoteVideoView.swift` and copies
only `remote.html`, `remote.css`, `remote.js` into the app's `media/` resources.
No desktop host/recording code or credentials are packaged. iPad uses inline,
muted video; the viewer denies camera/microphone requests. Screen idle prevention
is scoped to visible, foreground received video and released on suspension.
No audio relay, local recording, Meet automation, YouTube or background streaming
is implemented here. One active Remote viewer and one paired speaker remain the host limits; pairing
the iPad replaces the previous Mac Remote pairing. This does not remove either
laptop from Meet. The host may retain its capture lease for up to 15 seconds
after local suspension; PPT/Meet output continues.

## Build and local checks

Provisional deployment target: iPadOS 16.0 or newer; the owner's actual iPad
model/OS is still to be confirmed. No external Swift packages are required.

```sh
node tools/sunday-presenter/ipad/test.cjs
node tools/sunday-presenter/ipad/build.cjs --check-project
node tools/sunday-presenter/ipad/build.cjs --simulator
node tools/sunday-presenter/ipad/build.cjs --device
```

The first command compiles/tests the actual Foundation/Security client and scope
on the Mac SDK, validates plist/project/resources, and parses UIKit source.
It does **not** compile UIKit against the iOS SDK, launch an app or verify WebRTC
on iPad. The last two commands require full Xcode and the appropriate iOS SDK.
Simulator builds use Xcode ad-hoc signing, including its simulated application
identifier; disabling signing breaks Keychain pairing on the tested runtime.
Device builds remain unsigned and **not installable**. Generated projects are
deterministic (`generate-project.cjs`). Products default to the ignored `output/`
directory; set `SUNDAY_IPAD_DERIVED_DATA` to place DerivedData on an SSD.

## Install and actual acceptance

1. Install full Xcode with iOS support, then open
   `SundayRemote.xcodeproj`. A Command Line Tools-only installation is insufficient.
   If Xcode is at a nondefault path, use `DEVELOPER_DIR` for this build rather
   than changing the machine-wide selected toolchain.
2. In Xcode, select your Apple account/team and connected iPad. Complete any
   account agreements, device trust or Developer Mode prompts yourself. Never
   commit signing profiles, device identifiers or credentials. Xcode's normal
   Run flow handles development signing; this pilot does not register devices
   or create signing identities automatically.
3. Run on the iPad. Allow Local Network, connect it and the Presenter to the same
   Wi-Fi/hotspot, enable speaker remote on Presenter, and enter its current code.
   Camera/microphone permission should never be requested by this target.
4. First observe only; verify the real PPT, animation and deck switches. Check
   rotation, portrait/landscape, expanded viewer, keyboard appearance and readable
   type on the actual screen. Then explicitly enable control and test both buttons
   and external keyboard arrows, including errors and late callbacks.
5. Test app switch, lock/unlock, denied local network, Wi-Fi/hotspot interruption,
   Presenter restart/port change, re-pairing and host switching. Do not assert
   background video stays connected: the pilot stops it and reconnects on return.
6. Run a full service on the intended hardware/network before making it the
   speaker's sole viewer. Until then, retain the existing two-laptop Meet flow.

Apple references: [local network privacy](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy),
[inline WKWebView playback](https://developer.apple.com/documentation/webkit/wkwebviewconfiguration/allowsinlinemediaplayback),
[running on physical devices](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices).

## Current evidence and limits

On 2026-11-04, after the owner completed the authorized Xcode agreement and Mac
administrator prompt, `xcodebuild -checkFirstLaunchStatus` passed. Xcode 26.6
(17F113) is installed in `/Applications/Xcode.app`; macOS remains 26.5.2. The
machine-wide CLT selection is unchanged: builds use
`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`. Xcode itself has not
been moved to SSD. DerivedData is on `/Volumes/Jun SSD/SundayPresenterTests/iPadBuild`.

iOS 26.5 (23F77) runs on the iPad (A16) simulator. Both the simulator build and
unsigned physical-device architecture build succeed. Actual simulator UI checks
with a real Office PPT and a development Presenter passed:

- Six-digit pairing saves to Keychain; terminating/relaunching the app restores
  the pair without re-entering a code, and video returns.
- Observer mode starts on and disables navigation. Explicitly disabling it lets
  touch Next advance to the second slide and Previous return to the title slide.
- Expanded viewing hides connection details but retains navigation and settings;
  portrait and landscape render correctly after the rotation transition.
- Home/app return reconnects and restores the PPT image. Restarting the host and
  enabling Remote also re-discovers the saved host identity at its new endpoint.

The initial unsigned simulator run could not save pairing; this failed attempt
is superseded by the correctly signed simulator build, not counted as a pass.
The old installed host had a black PPT source after overnight idle; reopening
Office restored the source. Its destroyed-view close/quit error was reproduced
and fixed with regression tests in this branch. This does not establish an
idle-black-screen fix or full-service stability. The installed Mac app bundle
was not replaced: the current demo host runs development source.

All 53 Presenter/Remote regression tests pass, including 38 portable production
client/scope/TLS/redirect assertions and two destroyed-view counterexamples.
Earlier Mac Remote builds passed on both architectures. No physical iPad, real
Wi-Fi/hotspot interruption, minimum-iPadOS, external keyboard or full-service
iPad acceptance is claimed. No installable IPA or App Store/TestFlight release
has been produced. The owner's iPad model/OS and normal Xcode signing/device setup
remain the next hardware steps. Retain both laptops in Meet this Sunday.

### Physical-device setup and disk usage (2026-11-04)

The connected iPad Air (5th generation) reports iPadOS 27.2 (24B119).
Developer Mode, USB pairing and developer disk image services are enabled;
Xcode lists it as a real run destination. This confirms the development
connection, not app installation or hardware acceptance.

First connection created roughly 12 GiB of device-support data during processing;
the finalized source occupied about 6.5 GiB. Together with Xcode (~3.7 GiB), the
simulator runtime asset (~7.9 GiB), simulator caches (~3 GiB) and test-device data
(~2.4 GiB), this exhausted internal space at the observed peak. These are measured
directory sizes, not a complete before/after accounting of the owner's disk.
Closing Xcode and shutting down the owned test simulator recovered additional
space. No unrelated user files, recordings or photo libraries were removed.

DeviceSupport was copied to
`/Volumes/Jun SSD/SundayPresenterTests/iOS DeviceSupport`, checksum-verified,
resynchronized after Xcode finalized its symbols, and verified again before
removing the internal copy. The original
`~/Library/Developer/Xcode/iOS DeviceSupport` now links to that SSD directory.
Keep the SSD mounted for these development operations. DerivedData remains on
the SSD too; Xcode, the runtime asset and simulator caches remain internal.
Internal free space recovered from about 100 MiB to approximately 25 GiB.

After owner Keychain authorization, the signed physical-device build succeeded
and `codesign --verify --deep --strict` passed. `devicectl` installed Sunday
Remote 0.1.0 (build 1) on the real iPad. First launch was denied by iPadOS with
a signature/entitlement/profile-trust security message; explicit developer
profile trust on the device has been requested. Launch, pairing, PPT and real
network acceptance remain pending, not inferred from successful installation.
The profile expires on 2026-11-11 at 19:47 UTC (13:47 America/Chicago). This is a
time-limited development install, not a permanent distribution package.
No team identifier or personal signing settings are checked into the generated
project. Internal free space subsequently measured approximately 27 GiB.
