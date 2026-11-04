# Sunday Remote for iPad — pilot source (#652)

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
