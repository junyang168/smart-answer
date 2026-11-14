## #655 free automatic renewal pilot (2026-11-04)

Owner subsequently trusted the developer, opened the real iPad app and reported
working PPT/control on Wi-Fi and an iPhone hotspot. These are owner-reported
functional checks, not full-service or 90-minute stability acceptance.

Free Personal Team renewal was exercised with the existing Xcode account and
certificate. Backing up/removing only the matching cached profile made Xcode
issue a genuinely newer profile: expiry advanced from November 11 at 13:47 to
14:31 Chicago time. devicectl confirmed a successful wireless installation with
the iPad passcode-locked after first unlock. No USB, paid service or repeated
Keychain password was used. The deployed helper then completed another actual
locked Wi-Fi renewal/install, advancing expiry to 14:36.

The first real user LaunchAgent run could not access its SSD work directory
(`Operation not permitted`). No renewal was claimed from that failure. The
dedicated fixed snapshot/build workspace (~81 MiB initially) was moved into the
private user application-support directory; the large DeviceSupport cache stays
on SSD. A subsequent background run correctly reported `not_due`. An explicit
one-off launchd test then successfully built/signed a newer profile; its final
lock check observed iPad had been unlocked and deferred installation. Full
background installation is pending a locked-device retry at this checkpoint.

The hourly user LaunchAgent was installed and loaded during the test. It and the
one-off verifier were subsequently unloaded for a reported MacBook pairing
problem: discovery worked, the iPad requested a code, and the owner reported
recovery after pairing again. Signatures retain the same application/team ID;
the root cause of the unavailable/rejected old pairing is unproven. The client's
first-discovered-host selection can also choose Mac mini after foreground return.
Automatic updates remain disabled pending MacBook pair-retention/reconnect checks;
do not present the schedule as active or post-update pairing as accepted.

The helper checks actual
profile expiry, team/bundle/device, fixed source hashes, device lock/first unlock,
Sunday in America/Chicago, disk space, one-build/day budget and command timeouts.
Failed builds/installs cannot advance installed expiry; signed pending artifacts
are reused. Ten deterministic counterexample tests cover guard, failure, cache
restore, throttle and timeout behavior; all 54 top-level regression tests pass.
No Full Disk Access, credential sharing, certificate revocation or automatic
source-code upgrades were introduced. Mac sleep/logout, unreachable/never-locked
iPad and Apple/Keychain reauthorization can still prevent unattended renewal;
long-term/weekly operation and locked-Mac behavior are not yet established.
See [renewal operations](ipad/README.md#free-owner-only-automatic-renewal-pilot-655).

## #652 physical iPad installation; first launch awaits device trust (2026-11-04)

The owner connected an iPad Air (5th generation), iPadOS 27.2 (24B119), enabled
Developer Mode, paired it over USB, signed in to Xcode and authorized Keychain.
Xcode lists the real device and developer disk image services are available.
The signed Debug device build succeeds and strict/deep codesign verification
passes. Official devicectl installation confirms Sunday Remote 0.1.0 build 1.
First launch was denied with an iPadOS signature/entitlement/profile-trust
security error; the owner has been asked to explicitly trust their development
profile on iPad. No real-device launch/video/control pass is claimed yet.
The Personal Team profile expires 2026-11-11 19:47 UTC (13:47 Chicago).

DeviceSupport processing exhausted internal space during this setup. Its
finalized data was copied and checksum-verified on SSD before replacing the
internal directory with a symlink. Closing the owned test simulator and Xcode
also released space. Internal free space recovered to roughly 27 GiB. Measured
sizes and limits of the disk accounting are recorded in [iPad setup](ipad/README.md#physical-device-setup-and-disk-usage-2026-11-04).
No unrelated user data was deleted. Source project generation remains free of
personal team/signing settings; the installed app is a development build.

## #652 iPad simulator runtime verified (2026-11-04)

The owner completed Mac system authorization. Xcode 26.6 first-launch status now
passes; iOS 26.5 (23F77) boots an iPad (A16) simulator. Simulator and unsigned
physical-device architecture builds succeed. DerivedData is on the SSD; Xcode
remains in `/Applications`, with no global toolchain or OS change.

Real simulator UI verification against a development Presenter and actual Office
PPT passed pairing, video, observer-default disabled navigation, explicit touch
Next/Previous, expanded viewer, portrait/landscape, Home/app return and process
termination/relaunch with remembered pairing and recovered image. Saved host
identity also reconnected after host restart. The simulator initially failed
Keychain saves with signing disabled; normal Xcode ad-hoc simulator signing fixed
that failure. UIKit compilation also caught a stored `next` property conflicting
with UIResponder; it is now `nextButton`.

The old installed 0.6.1 host's Office source was black after overnight idle; a
fresh Office presentation restored real video. Close/quit also reproduced missing
`webContents` during destroyed-view callbacks. Stage now handles that lifecycle,
removes closed pages before notifying observers, and preserves another live deck.
Two regression counterexamples pass, and all 53 regression tests pass. No claim
is made that the idle black source is resolved. Installed app bytes were not
replaced; the currently running demonstration host uses this worktree source.

No physical iPad, minimum-iPadOS, hardware keyboard, real hotspot/network change
or full-service iPad acceptance is claimed. No microphone, recording, Meet or
YouTube session was started. Keep the existing two-laptop Meet service workflow.
See [iPad build and acceptance](ipad/README.md). Earlier entries below retain the
failed/intermediate evidence and are not the current setup status.

# Sunday Presenter acceptance evidence

## #652 agreement confirmed; administrator authorization required (2026-11-04)

The owner explicitly replied agreeing to the displayed Xcode and Apple SDKs
Agreement. The normal Xcode UI's Agree button was pressed. It became disabled,
but first-launch status remains exit 69 and SDK lookup still reports that the
license has not been recorded. Xcode 26.6 / build 17F113 is readable; that fact
alone does not establish successful first-launch setup.

The computer-use tool explicitly refuses access to com.apple.SecurityAgent for
safety reasons. An independent supported CLI path was checked with the owner's
agreement authority: `xcodebuild -license accept` reports that administrator
privileges are required. A noninteractive administrator attempt fails immediately
with "sudo: a password is required". No password was read, entered, guessed or
requested in chat; no authorization/security setting was altered. Further
first-launch setup needs the owner to complete the Mac's system authorization.
Simulator installation/build/run and SSD relocation remain pending. Source and
installed Presenter behavior are unchanged; the existing tests do not become
an iPad runtime pass. The prior pending-agreement observations below are history.

## #652 Xcode installed; first-launch agreement pending (2026-11-04)

The owner authorized installing Xcode and the iPad simulator. The signed-in Mac
App Store offered its last compatible Xcode release because the current listing
requires macOS 26.6. After the normal Download confirmation, installation completed
and the app's Info.plist identifies version 26.6. It is currently in
`/Applications/Xcode.app`; macOS remains 26.5.2, and no OS update was initiated.
The installed bundle occupies about 4.0 GiB of allocated disk blocks; the internal
volume still showed about 36 GiB free after installation. Relocation to SSD is
pending first-launch setup; no completed SSD move is claimed.

Opening Xcode showed its Xcode and Apple SDKs Agreement, identifier EA1990 dated
2026-01-21. Agree has not been pressed; explicit action-time confirmation was
requested, separately from software-install authorization. No password, account
agreement or security control was bypassed. Simulator setup, iOS compilation and
actual app testing remain outstanding. Earlier missing-toolchain observations
below are historical; they are not a claim that Xcode is still uninstalled.

## #652 iPad-first pilot source — not an iPad build (2026-11-04)

The owner identified iPad as the primary Remote target. The iPad-only UIKit
project reuses the existing bundled WebRTC receiver and host protocol. It provides
Bonjour host selection, per-host Keychain pairing, exact-certificate/endpoint pin,
no HTTP redirects, touch/keyboard controls with live binding, observer mode on
by default, expanded viewer and foreground reconnection. Background/resign-active
stops the local viewer and cancels the URLSession; the host's existing lease can
expire within 15 seconds. No remote microphone, recording or streaming outlet
was introduced. The installed Mac apps and their permissions were not changed.

Available validation: all 51 unit/regression tests pass, including 38 iPad
production-scope/TLS/redirect assertions. The actual Foundation/Security client
compiles on the Mac SDK; production scope and TLS-delegate tests exercise a generated real test
certificate, wrong pin/endpoint/port, missing trust, redirects, host-switch/stale
state rejection, observer gating and background invalidation. Project/plist and
resource paths validate, UIKit source parses, and both existing Mac Remote
architectures build. These checks do not type-check UIKit against an iOS SDK or
establish iPad WebRTC/permissions/layout behavior.

The development Mac has Command Line Tools only; no Xcode was found by application
inventory/Spotlight, and both iphoneos and iphonesimulator SDK lookup fail. The
simulator build command correctly exits nonzero instead of producing an artifact.
Actual iOS build, signing, installation, hardware/network and full-service
acceptance remain outstanding. The first-Sunday plan stays unchanged until those
checks finish. Source, commands and Apple references are in `ipad/README.md`.

## #643 refined three-step rollout (2026-11-04)

The owner clarified that this Sunday is an observational first step: Presenter
switches PPT/OUTPUT; both host and speaker laptops stay in Meet, keeping the
existing service/audio workflow. A separate device observes Remote video without
issuing live slide commands. Step 2 introduces speaker-operated Remote and the
existing wireless microphone feeding the host's Meet; only the host joins Meet.
Step 3 adds YouTube after those paths stabilize. The v4 design and instructions
supersede the earlier wording that put speaker Remote/wireless mic into the first
step. This is a rollout plan, not evidence those service steps already passed.
Recording stays optional/inactive throughout. No runtime logic or installed app
was changed, and no meeting, microphone test or streaming session was started.

## #643 owner rollout decision — Meet first, recording optional (2026-11-04)

The owner directed continued Google Meet use while Presenter/Remote stabilizes,
with YouTube Live deferred. Current runtime already starts recording only from
an explicit `record-start` action: app startup, PPT playback and Remote connection
do not start it. Meet-only use does not require Presenter microphone access or
an MP4 save location; Meet has its own input/permission controls. Controller copy
now states that recording is optional and initially inactive. This is wording
and rollout-order clarification, not a newly implemented automatic-recording
switch. No package reinstall, permission reset or new live call was needed.

The v3 design requires an independent, default-off setting if future automatic
recording is added; recording disabled/failed must not block Meet, PPT, Remote
or later YouTube. Meet/target-Mac/hotspot/full-service acceptance is the current
priority. The two short digital-silence findings below remain unresolved;
recording is neither accepted as clean audio nor a prerequisite for Meet.

## #643 completed 90-minute installed-app run — qualified result (2026-11-04)

Installed 0.6.1 (runtime commit `0697a29b`) recorded the owner's actual Office
slideshow and explicitly selected AudioPro X5 into an external SSD, from
2026-11-03 22:47 to 2026-11-04 00:17 America/Chicago. Normal Stop/Save displayed
saved; the final file exists and no partial remains. The independent monitor
reached its 90-minute deadline without a process loss or file-growth alarm.
Its 91 minute checkpoints show a peak combined app/helper RSS of 1,277 MiB
and an ending RSS of 940 MiB; these are sampled observations, not a leak proof.

Final recording: `PPT-AudioPro-90min-061-20261103-2247.mp4`, 1,327,132,068 bytes,
SHA256 `6a50a5ddf669c61b8b8f413d40993e77d628ccdb22372c62a4e165b5c7a3a245`.
AAC is 5,417.023500 s and 1280×720 H.264 is 5,417.048567 s: a 25.067 ms
duration difference. Complete audio/video decoding reports zero errors; all
253,923 audio and 164,153 video packets have timing, no backwards DTS, and
maximum positive gaps of 0.688 ms audio / 2.834 ms video. All 90 minute-spaced
visual samples were inspected, including the seven UI interaction markers:
audience slides and animations progress through slide 7, with no captured
controller or sustained black output. QuickTime opened the final file, played
the beginning and middle, and played the final seconds to its normal end.

**Overall uninterrupted-sound acceptance is NOT passed.** Every minute has
nonzero audio, but a finer decoded-sample inspection found two digital-silence
intervals: 205.824875 s for 124.750 ms and 1,734.186500 s for 128.250 ms.
Independent 48 kHz stereo extraction confirms both channels are silent there;
there is no corresponding MP4 timestamp discontinuity. The saved file cannot
identify whether this originated in the wireless receiver, USB/OS capture or
audio processing. There was no simultaneous pre-encoder reference capture, so
attribution and speech intelligibility remain unresolved. Decoded AAC also has
101 / 43,336,192 mono 8 kHz samples at or above unity; lossy-codec overshoot is
not proof of source clipping, and input gain still needs a real speech check.

The original packet/decode inspector labeled the file passed because it did
not gate decoded silence. That original result is preserved, not overwritten.
The inspector now separately reports media-timeline success and signal review,
and refuses an overall pass on ≥100 ms digital-silence intervals. Re-analysis
of the same immutable file fails that gate as expected. New counterexamples
cover the observed 128.25 ms run, trailing silence, ordinary zero crossings
and AAC overshoot. All 50 unit/regression tests pass. No packaged runtime was
changed after this recording; only the analysis tooling and evidence changed.

Private evidence: ignored `output/endurance-061-20261103-2247/` (monitor,
interaction markers, original `analysis/`, stricter `analysis-signal-strict/`,
stereo zero-run confirmation and contact sheet); final MP4 under the owner's
SSD `SundayPresenterTests` directory. `dist/endurance-0.6.1-manifest.json` binds
the build, recording and analyses. Recording/finalization/video endurance is
demonstrated; a clean-audio whole-service claim is withheld. The next diagnostic
must compare pre-encoder physical-input samples with normalized/encoded samples
to locate these short silences before another clean-audio acceptance run.
Nothing was uploaded, streamed, deployed or published as a website download.

## #643 whole-service endurance follow-up — 0.6.1 candidate (2026-11-03)

Owner requested a full-service-duration recording. The first installed 0.6.0
attempt used real Office PPT + AudioPro and an external SSD, starting at
22:24:26 America/Chicago. It failed at about 46.46 seconds: the five-second
encoder no-output guard fired. An independent file-growth monitor confirmed
stalled writes; the app remained alive and SSD free space was about 197 GiB.
The partial is retained. This is a failed endurance result, not a 90-minute pass.

A separate 150-second actual AudioPro repro exposed a measurable clock defect:
audio 148.500917 s versus video 150.337667 s. The previous short hardware smoke
only checked codecs/decodability, so its old PASS label does not establish sync;
the tightened skew gate rejects this saved counterexample.

The recording audio now passes from the exact selected physical source through
a 48 kHz Web Audio MediaStreamDestination before encoding. No speaker connection
is made; source-ended still stops/finalizes, and original/normalized tracks plus
the context close on stop. Audio and canvas retain their native capture timestamps.
The corresponding 150-second hardware test yields audio 150.420708 s and video
150.448433 s (~28 ms), independently decoded without errors. A real Office PPT +
AudioPro + same-SSD repro then ran 229 seconds and finalized normally: audio
229.098042 s / video 229.119467 s (~21 ms), zero full-decode errors, no backward
DTS, maximum positive packet gaps about 0.69 ms audio / 1.73 ms video. These are
short corrected checks; the full 90-minute run remains required.

Capture/encoder failure diagnostics retain track/recorder/context/timer states,
without document URLs or audio samples. A read-only endurance monitor checks
file growth, early finalization, process survival, disk space and RSS. Its separate
inspector streams all packet timestamps, fully decodes video/audio, writes one
visual sample per minute, and summarizes decoded audio in bounded per-minute bins.
It fails on insufficient track duration, >350 ms A/V duration difference,
backward DTS, missing timing or significant packet gaps. This measures the file's
media timeline, not acoustic latency or speech intelligibility.

48 unit/regression tests pass, including clock-normalization routing and cleanup,
physical-device binding/source loss, and rejected packet-gap/skew counterexamples.
Raw evidence stays in ignored output and on the owner's SSD. No website release
or deployment occurred. Full endurance verdict will be appended after recording,
finalization, full decoding and visual inspection actually finish.


## #643 local PPT + host microphone recording — 0.6.0 (2026-11-03)

Authorized next milestone: a local video file before YouTube/Meet streaming.
The controller enumerates/selects a precise microphone, chooses an MP4 path,
starts/stops recording, shows input level/progress, and reveals the result.
No network publication or Remote audio was started. Device enumeration requests
OS consent but opens no track. Start verifies the actual exact deviceId, with
no fallback. OUTPUT capture is shared with Remote; the recording branch draws
the same track through a 1280×720, 30 fps canvas and combines it with one microphone
track. Audio is never routed to speakers by the recorder.

Two actual counterexamples changed the implementation. Sparse ScreenCaptureKit
frames caused the initial video track to end about a second before audio; canvas
sampling fixes the still-frame timeline. A timeslice alone did not flush MP4
before stop; matching 500 ms keyframe interval/timeslice now produces incrementally
readable audio/video packets. That bridge was independently read with ffprobe
before stopping the encoder. There is no production FFmpeg dependency or silent
codec fallback. H.264/AAC support is checked before capture.

File data is streamed with strictly ordered, trusted-main-frame IPC. Renderer and
writer queues reject more than 16 MiB; the two-second backlog and five-second
encoder-output checks stop a failed branch. Finalization waits for encoder stop,
queued writes and fsync, then reserves a new output name and renames its partial
file. Existing files are never overwritten. Storage/encoding/renderer failure
reports an error and retains a partial file. Source/preparation/minimize loss and
microphone-ended stop and finalize the existing clip. Restart is explicit.
Startup cancellation and reentrant source-loss cleanup cannot open a late mic.
Quit finalizes first and guards repeated cleanup; an old installed build exposed
a repeated-quit destroyed-window error, fixed in this pilot.

Validation:

- 43 tests pass, including shared capture ownership/cancellation, write ordering/size, ENOSPC preservation, name
  collisions, startup cancellation, reentrant source loss, late/untrusted IPC,
  concurrent recording intent and preserved permission-error messages.
- Full capture/recording smoke passes with actual OUTPUT and generated reference
  audio. In the final two-minute run, independently decoded video is 126.059533 s
  and audio 126.036771 s (about 23 ms duration difference). A PPT color switch and
  tone-frequency switch occur at 2.50 s / 2.45 s (about 50 ms; visual sampling is
  250 ms, so this is a bounded marker check, not a precision latency measurement).
- Both H.264 and AAC decode without ffmpeg errors. The smoke checks pre-stop MP4
  packets, same capture track during a Remote offer, Remote teardown preserving
  recording, source privacy auto-finalization, microphone-ended finalization,
  repeated recording and released tracks. Raw evidence: ignored
  `output/smoke/recording-result.json`; recordings under
  `output/smoke/recording-1793762196212/`.
- Actual AudioPro X5 USB input was explicitly selected in the development runtime
  and recorded for ten seconds, using 48 kHz/two channels. AAC/H.264 independently
  decode; measured nonzero RMS is about 0.000538, a very quiet input. This proves
  hardware capture, not intelligible human speech or the transmitter's battery
  health. Evidence: ignored `output/smoke/hardware-recording-result.json`.
- QuickTime opened a final short MP4, played to its end and showed the changed
  PPT/animation state. Existing full native Remote video/fullscreen/button/arrow,
  20-switch/privacy/reconnect smoke passes after the shared-capture refactor.
- Both native architectures build; final DMG/ZIP/signature and installed-source
  checks are recorded with `dist/local-recording-0.6.0-manifest.json`.
  DMG SHA256: `4043b2c1955119058282671a27a99e68423cc85bdafd03fd9c9ff359d7720285`. Remote 0.5.3 remains
  compatible, so the owner only needs the new Presenter for recording.

Installed-app verification is separately bounded. The final 0.6.0 controller and
wireless microphone selection were exercised through normal UI, and the retained
Microsoft session opened the owner's real PowerPoint slideshow without edits.
An initial recording failed because TCC rejected the old ad-hoc ScreenCapture code
requirement; this is not a passing recording. The stale Presenter-only screen
entry was removed/re-added through System Settings for the final canonical
`/Applications/Sunday Presenter.app`. Microphone consent had the same stale-signature
failure; only this app's Microphone status was reset and a new prompt requested.
The computer-use tool is prohibited from operating UserNotificationCenter; the
owner explicitly completed the fresh microphone consent. The final installed
0.6.0 app then recorded the real Office slideshow and actual AudioPro X5 through
the normal UI, using the Save dialog and Stop/Save button. The app showed active
recording, live input level and growing file size. Two native Right presses
advanced the real slideshow without stopping recording. Stop/Save visibly
reported the final MP4, with no recording left running.

The actual clip is 33.518533 s / 10,153,997 bytes: H.264 1280×720 video and AAC
48 kHz/stereo audio. Independent full decoding reports zero errors. Audio duration
is 33.194042 s (about 325 ms shorter than video); decoded RMS is 0.003089 and peak
0.137600. This is measured signal, not an intelligibility or acoustic-sync verdict.
Extracted frames at 5 s and 27 s visibly contain different audience slides, with
no controller/editor UI. QuickTime opens the file as a 34-second movie and plays
it. Private evidence: `output/smoke/installed-recording-result.json` and
`installed-real-ppt-5.png` / `installed-real-ppt-27.png` in the same ignored directory.
The local real recording is in the owner's Movies folder; it was not uploaded.
Old app bundles remain preserved under local state. No TCC database editing or
system protection disable occurred.

Remaining: spoken microphone/acoustic-sync acceptance, whole-service duration,
target MacBook/hotspot performance, and
YouTube/scheduling/standalone test-mic/mute features. A local two-minute file does
not establish 90-minute drift/resource behavior or broadcast readiness.


## #643 Remote viewing stability and compact controls — 0.5.3 (2026-11-03)

After updating to 0.5.2, the owner confirmed Pro/Air pairing over iPhone Personal
Hotspot works, but reported a later interruption which recovered after clicking.
This report establishes hotspot pairing; duration and full service acceptance
remain open. The precise cause of that physical interruption was not logged.

A deterministic regression demonstrates two defects in the previous viewer:
a connected still slide with no additional decoded frames is treated as failed
after eight seconds, and even a brief ICE disconnection immediately tears down
the session. Both counterexamples fail against 0.5.2 and pass against 0.5.3.
The viewer now retains still slides, automatically resumes paused video, allows
five seconds for transient ICE recovery, and still retries persistent failure
or a 25-second first-frame timeout. The native heartbeat uses common run-loop
modes; a scoped user-initiated/display-awake activity is held only while live
playback is visible, and released on disconnect, switch, close, minimize or quit.
This reduces idle scheduling/sleep risks without claiming to fix every hotspot
drop. Apple references: [activity options](https://developer.apple.com/documentation/foundation/processinfo/activityoptions)
and [primary fullscreen](https://developer.apple.com/documentation/appkit/nswindow/collectionbehavior-swift.struct/fullscreenprimary).

The large heading is removed. Video fills the upper window, with host/status,
pairing (only when needed), slide controls and Full Screen below. Native primary
fullscreen replaces the old auxiliary floating-window configuration; the button,
green window control and Control-Command-F are available.


Validation on Apple Silicon macOS 26.5.2: 31 tests pass; both native targets
build. Native fullscreen entry/exit passes, the viewer occupies more than 85%
of content height (about 89% at the default size), controls are below the viewer,
and the display-awake activity is present during live playback. A three-minute
actual OUTPUT → native WebKit stream left completely untouched retained the same
session. The subsequent 20-switch, native button/viewer-arrow, privacy stop,
source recovery and reconnect smoke checks also pass. Activity creation/release also passes the compiled native regression.
Visual inspection confirms the compact footer and removed heading.

The Remote-only universal 0.5.3 ZIP passes integrity and deep/strict signature
checks; the extracted media matches source and the executable includes x86_64
and arm64. ZIP SHA256:
`de11f394cd50bbf1f5f176b123a502df06ff04f56230d37fa02829d5a3bd0a7e`.
Presenter 0.5.1 remains compatible; no website publication or host update.
Physical hotspot idle duration and whole-service reliability remain pending.

## #643 switching Presenter hosts — Remote 0.5.2 (2026-11-03)

The owner confirmed two physical Macs connected on the same home network. When
moving Remote to a MacBook Pro Presenter on iPhone Personal Hotspot, the screenshot
showed the Pro in the service selector but no endpoint. Native resolution returned
early whenever the advertised certificate identity differed from the remembered
Mac, including after explicit host selection. This is a proven client defect;
its repair does not yet establish two-Mac hotspot acceptance.

Remote now resolves only the selected host and uses its advertised identity for
certificate pinning. It reuses the saved token only for that exact identity;
another host requires its own pairing code. The prior saved pair remains intact
until a new pairing succeeds. Host switching clears command/video state and
invalidates late responses; the host selector stays visible when connected.

A compiled native regression exercises the actual RemoteApp with two synthetic
Bonjour services: old saved pairing → new host, old-token isolation, ignored
background resolution, return to the saved host, cleared controls/busy state,
and a delayed old-host response after switching. Both native architectures build.
The complete 27-test suite passes; the extended delayed-response check also
passes. Remote 0.5.2 is compatible with Presenter 0.5.1 and packaged separately;
no website installer update or Presenter permission change is needed.

The full Electron → native WebKit video smoke also passes: 1280×720 video only,
20 switches preserving the session, native buttons and viewer-focused arrows,
privacy stop, source recovery and reconnect. ZIP integrity and deep/strict bundle
signature verification pass. The Remote-only universal ZIP SHA256 is
`6e9dd499b66837f6b6595a2369178ea913b4229d5c3f17ec0838d8d68b40b273`.
Actual Pro/Air hotspot pairing, video and controls still need owner verification.


## #643 embedded slide-control fix — 0.5.1 (2026-11-03)

The owner reported working video but no Remote slide response in a real Office
slideshow. Direct host keyboard input advanced its animation. A new opaque
iframe fixture reproduced the failure: WebContents/frame focus reported success,
but Electron `sendInputEvent` delivered no keys to the child document.

The host now restores the confirmed playback frame and uses a temporary,
in-process Chromium input session for embedded frames. It checks the current
Stage binding again before delivery, sends one key-down/up pair, then detaches;
no global OS keystrokes or debugging network listener is added. Top-level
presentations retain the ordinary input path. API references:
[Electron keyboard input](https://www.electronjs.org/docs/latest/api/web-contents),
[Electron in-process debugger](https://www.electronjs.org/docs/latest/api/debugger).
Remote's arrow filter now excludes only the visible pairing editor, and command
errors remain visible for eight seconds instead of being overwritten by polling.

Validation: the embedded-frame counterexample and retained presentation smoke
pass. The native Remote video smoke now exercises the actual NSButton action and
a queued arrow event with WKWebView focused; each advances exactly once. Local
720p receive, 20 deck switches, source stop/recovery and reconnect also pass.
The 26 existing protocol/presentation tests pass. Reproduce with `npm run smoke`
or `npm run smoke:video`; the isolated smoke shortcut avoids the installed app's
Command-P binding. No microphone, recording or YouTube feature was added.

The local 0.5.1 DMG/ZIP, installed host/Remote signatures, ZIP integrity and
packaged/installed source comparisons pass. The two /Applications apps were
updated with originals preserved in the private `ops-643` local-state backup.
The existing Microsoft session and host pairing identity were retained, and the
owner's current PPT was reopened at slide 6 without editing its contents.

After the owner completed macOS Keychain authorization, actual installed Remote
control passed on the owner's real Office slideshow: its Next button advanced
slide 6 to animation 1/3, then an arrow with the Remote viewer focused advanced it
to animation 2/3. The PowerPoint accessibility state independently confirmed both
steps. After screen capture was renewed, the same tests passed again with live
video: all three animations, slide 6 → 7, and the Previous button back to 6.
No manuscript or slide contents were edited.

The installed host's screen-recording grant needed renewal after the ad-hoc
update. macOS logged `Failed to match existing code requirement` for this bundle
and ScreenCapture; toggling/restarting alone retained that stale requirement.
Removing the old entry and re-adding the installed /Applications Presenter through
System Settings, then restarting it, restored actual Remote video. Only this
existing grant was renewed. Pairing and Microsoft credentials were retained; no
keychain ACL or website release changed. A SHA-bound local record of installed
versions and the actual Office actions is kept outside Git at
`~/.local/state/sunday-presenter/ops-643/actual-office-0.5.1.json`.
Second physical Mac/hotspot validation and the existing full-docs/frontend
dependency failures remain outside this change.

## #643 local PPT video pilot — 0.5.0 (2026-11-03)

Scope: video from the unique Presenter OUTPUT to native Sunday Remote, with the
existing paired slide control. No microphone capture, recording, YouTube or
schedule is implemented. The owner uses a wireless mic on the host; Remote mic
relay is deferred, PPT audio is excluded, and later YouTube work uses a reusable
stream key rather than OAuth/API lifecycle management.

Local evidence on Apple Silicon macOS 26.5.2 / Electron 45.2.1:

- 26 Node tests cover retained playback/pairing, authenticated video signaling,
  rejection of audio/data/public ICE candidates, SDP bounds, setup deadline,
  idempotent answers, stale session cancellation, bounded replacements and closed
  services rejecting delayed restarts.
- Native Remote compiles for arm64 and x86_64. Runtime validation below uses the
  arm64 slice and system WebKit on this Mac, not a claimed minimum-OS matrix.
- `npm run smoke:video` exercises actual native window capture and native
  WKWebView receive on the same unlocked Mac with disposable TLS pairing.
  Remote decodes 1280×720 video; pixel samples distinguish the two fixture decks
  (sermon `[111,47,25,255]`, worship `[17,60,79,255]`). Selected ICE candidates are
  host/host, and both sender and receiver have zero audio tracks.
- Twenty deck switches retain one media session and OUTPUT. A native Remote
  button advances the host's actual fixture slide. Moving the active page to
  private preparation and minimizing OUTPUT stop capture; re-confirming the
  source and viewer reconnection resume with fresh sessions and no new pairing.
- An additional active-shutdown smoke stops a live capture before destroying its
  renderer; the closed service rejects a delayed viewer restart and the app exits.
- The full retained playback smoke also passes popup opener/POST, controller
  focus, duplicate slide commands, navigation/fullscreen/crash invalidation and
  source identity checks. It does not sign in to Microsoft or Meet.

Reproduce with an unlocked desktop and no installed Presenter running:

```sh
cd tools/sunday-presenter
npm ci
npm test
npm run smoke:video
```

Raw evidence and viewer image: ignored `output/smoke/video-result.json` and
`output/smoke/remote-video.png`. Tests use synthetic slides in the real OUTPUT,
not a camera, desktop fallback or mocked video track. The test probe's token is
passed over stdin and is not saved in the user's Keychain or package.

Local package verification also passes: universal Remote build, ad-hoc deep/strict
signatures, DMG checksum and both ZIP integrity checks. The host ASAR's capture,
signaling and renderer source bytes match this checkout; test/output/native build
directories are excluded. Checksums are saved in ignored
`dist/local-pilot-manifest.json`. These packages were not installed or published.

Pending before field use: second physical Mac, iPhone hotspot/Bonjour, real Office
slides/animations, all target OS/CPU versions, full-screen keyboard focus and
long-duration behavior. 0.5.0 is a local pilot, not an installed production update.
No YouTube stream or audio recording was started; the Phase 1 release below is
unchanged. Frontend type-check remains blocked by inherited missing `cytoscape`
and `react-quill-new`; no frontend sources or dependencies changed.

## Current release scope — Phase 1, 0.4.0 (2026-11-03)

The owner explicitly split delivery: #598 is Phase 1 production playback and
local paired slide control, with Google Meet operated manually. #643 is Phase 2
YouTube broadcasting, speaker audio in Sunday Remote and automated opening.
Earlier Meet automation/scheduled-service notes below are historical, superseded
scope; their pending automation checks do not describe shipped Phase 1 features.

The Phase 1 source removes host workflow/helper/scheduler, all Meet actions and
settings loading, native Meet/Accessibility and system-output mutation, Remote
microphone/recording handling and its report route. No timer reads old service
settings. The Remote protocol returns only deck/live/binding; it carries no room
URL or broadcast command. Presenter no longer embeds the obsolete native helper.
Meet joining, sharing, audio and recording are explicitly manual in App guidance,
installer instructions and website download copy.

Verification completed before release:

- All 19 retained Node tests pass (presentation, folder/API, TLS pairing,
  restart, deduplication, stale commands and removed report/workflow routes).
- Real Electron smoke passes 20 retained-view switches, keyboard focus,
  operator/remote controls on the same page, popup opener/POST, cookies,
  mute/privacy/navigation/crash guards and single-window/display invariants.
  Former Meet IPC actions fail with Unknown action; no workflow, scheduler or
  meeting state/controls are present. No Microsoft or Meet sign-in was performed
  by that synthetic smoke.
- Nine website download/publication/authentication tests pass, including
  released metadata, unknown-status rejection and immutable publication.
  Frontend tsc --noEmit passes.
- Both native Remote architectures compile. Installed Remote's real network
  probe reads paired TLS state; a deliberately wrong certificate pin is rejected.
  That probe is protocol verification, not a new two-physical-Mac field test.
- Installed 0.4.0 Presenter opens with the existing user profile/settings and
  shows Phase 1 without Meet controls or permission prompts. Installed Remote
  displays discovery/pairing and two slide buttons only. Private startup proof:
  040-phase1-startup.png. Both installed bundles pass deep/strict signature checks;
  final DMG checksum and Remote ZIP integrity pass.
- The owner's earlier real Main Laptop OUTPUT-sharing and deck-switching report
  remains applicable to the retained presentation path. Actual Office files,
  hotspot/AirPlay conditions and manually operated Meet audio should be checked
  before a service; no new broad physical/network acceptance is claimed.

Installers remain ad-hoc signed without Developer ID/notarization. Phase 1 no
longer uses Accessibility, so its previous grant persistence no longer gates
playback or remote control. Local-network access and Chrome's own manual capture/
microphone permissions remain ordinary setup. This does not claim notarization.

## Historical evidence (earlier prototype scope)

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


After the owner completed Open Anyway, the downloaded /Applications 0.3.2
launched with the new playback controller. Process-path/version readback verified
that exact installed copy. Its single weekly-folder action reused the retained
Microsoft session and automatically opened/assigned both real 11/8 PPTs. Each
was put into Office slideshow mode and confirmed/played through the new primary
button. The installed app was left open with both decks ready and the controller
visible for owner testing. Downloaded-install startup is therefore verified
following an owner-granted Gatekeeper exception; this is not notarized first
launch, and Meet/physical two-Mac acceptance remains pending.


## Covered controller shortcut — 0.3.3

The owner reported that Command-P required two presses. A visible controller
could remain behind the native fullscreen output after switching or key delivery;
the old toggle treated it as foreground and hid it on the first press. The toggle
now hides only a visible, focused controller. Otherwise it restores a minimized
controller, shows it, raises its z-order and focuses it. Existing macOS fullscreen
workspace visibility remains enabled.

The Electron regression deliberately leaves the controller visible but unfocused
behind the actual fullscreen output, then verifies that exactly one toggle makes
it visible and focused, and the next hides it. This passes alongside the complete
20-switch/key/POST/fullscreen smoke. This is a 0.3.3 correction, not a claim that
the already-running installed 0.3.2 has been patched in memory.


The Mac was subsequently updated to the verified 0.3.3 bundle, preserving a
0.3.2 backup and user data. Native UI verification reproduced the covered case
by showing OUTPUT while the controller remained open, then sent one Command-P:
the focused window became the controller. A second press hid it, and another
single press restored it. This is installed-app shortcut verification, in
addition to the isolated Electron regression. It used the locally built verified
update, not another downloaded-package Gatekeeper first-launch test.


## Floating controls and no blank startup — 0.3.4

The owner superseded the hidden-shortcut controller design: no blank startup
window, no manual blackout UI, no fullscreen controller. Startup now allocates
OUTPUT hidden, opens a non-fullscreen settings panel, and enters fullscreen only
when a verified slide is taken. Internal end/navigation/failure gates mute views
and hide OUTPUT instead of presenting an empty user window; normal deck changes
retain its visible identity. The black/mute button, menu and Command-0 action are
removed. A second launch brings forward the panel rather than opening blank OUTPUT.

On macOS the controller uses the native floating panel window type, with explicit
regular Dock activation policy. Playback reduces it to 420×240. Settings expands
it without fullscreen. Close/Hide affects only the panel; Dock activation restores
it. Command-P only restores the panel. Command-1/2 are intercepted in Office views
before the web editor can consume them, and work while the panel remains closed.

The full Electron smoke passes: startup OUTPUT is hidden/non-fullscreen;
controller fullscreen is disabled; first verified slide enters fullscreen;
20 deck changes retain the capture window; popup POST/opener, audio isolation,
remote keys and invalidation safety pass. Closing the actual panel preserves the
live slide; app activation restores it; actual view key events for Command-1/2
switch decks without reopening the dismissed panel. Physical Dock clicking,
real installed-Office floating behavior and Meet capture for this new window
lifecycle require their specific checks; the emitted app activation is not a
physical Dock click.

The installed /Applications 0.3.4 bundle was subsequently compared with the
source ASAR and passed deep/strict signature verification. Native UI opened the
actual two weekly PPTs using the saved Microsoft session, started both Office
slideshows and confirmed playback. The controller reduced to the small floating
panel. Closing its native close button left the actual slideshow visible;
Command-2 selected the sermon and Command-1 returned to worship without opening
the panel. Command-P restored the compact panel and its status confirmed worship.
Private screenshots record the compact panel and full-screen real slides.

Read-only Dock preferences show the running /Applications app in recent apps.
Finder's Add to Dock was attempted, but a persistent pin and physical Dock click
were not verified: the computer interface could not inspect the Dock. Do not
treat that attempt as completed pinning. The emitted activation regression above
continues to cover the app's restore callback. Meet capture after this lifecycle
change and physical two-computer/audio acceptance remain pending.


## Settings only; automatic hide on playback — 0.3.5

The owner rejected the daily flip panel because it would be hidden during a
presentation anyway. The compact mode, settings/compact toggle and local
previous/next buttons are removed. There is one settings window. Every successful
Stage.take, including a workflow/scheduled take of the already-active deck,
hides settings and returns keyboard focus to the active Office view. State-only
updates do not hide a settings window that the operator intentionally reopened.
Dock activation and Command-P reopen full settings; close leaves playback running.
Return to playback is unavailable before any presentation exists.

All 32 Node tests pass. The updated Electron smoke checks automatic hiding from
the actual confirmation button, the direct scheduled-workflow take path, absence
of the flip controls, full settings restoration, Command-1/2 with settings hidden,
20 retained-state changes and the existing popup/session/audio/remote invariants.
An intermediate installed build also opened the actual weekly files and confirmed
that playing the real sermon automatically hid settings. Native preparation
focus required retaining the macOS panel type for the full settings window; this
is a window behavior detail, not a compact playback panel. Final source/installed
ASAR and package integrity are verified separately before publishing. Physical
Dock clicking, Meet lifecycle and two-Mac/audio acceptance remain pending.


Final installed 0.3.5 native UI then opened the actual upcoming-week folder,
entered the real worship slideshow and confirmed playback. The focused native
window became OUTPUT without an extra hide action. The settings window did not
appear in the saved playback screenshot (`035-playback-without-controls.png`,
private evidence directory). Both source and installed ASAR matched; deep/strict
signature, final DMG checksum and Remote ZIP integrity passed.


## Window-sharing omission reproduced; compatibility candidate — 0.3.6

The owner reproduced missing OUTPUT in the main laptop's Meet Window picker.
The PPT itself displayed correctly; Chrome's Screen & System Audio Recording
permission was enabled and a full Chrome restart did not resolve it. On this Mac,
the actual Chrome getDisplayMedia Window picker likewise omitted the installed
0.3.5 native-fullscreen OUTPUT while other app windows were listed. A private
screenshot records the missing-source list. This observation motivates avoiding
a separate fullscreen Space; it does not yet prove the cause on every Mac.

A simple-fullscreen candidate again lost slide key delivery on the second deck
and was rejected. The 0.3.6 candidate instead keeps OUTPUT as one normal borderless
window, covering the exact display x/y/width/height, with larger-than-screen
bounds enabled. No native-fullscreen Space is entered on macOS, including during
Office HTML fullscreen. The same native OUTPUT ID remains across all deck
changes. No second output, screen-wide capture fallback, cookie transfer or
security-permission change was introduced.

All 32 Node tests and the complete Electron smoke pass for this candidate,
including exact display bounds, no native fullscreen, 20 retained-state switches,
key focus, one capture-window identity, HTML fullscreen, hidden settings and the
existing remote/audio/popup invariants. Installed source/ASAR, deep/strict
signature, DMG checksum and Remote ZIP checks pass. The installed candidate opened
the actual weekly worship slideshow and displayed it with settings hidden.

A second Chrome chooser run could not be completed through computer automation:
both the local diagnostic and WebRTC's official getDisplayMedia sample returned
InvalidStateError before displaying the picker. This is not a missing-window
result and not evidence of successful capture. Version 0.3.6 is a pilot candidate;
the target laptop's Meet picker and continuous capture across worship/sermon
switches still require owner verification. Physical two-Mac/audio acceptance is
unchanged and the PR remains draft.


## Automatic join reproduced and verified — 0.3.7

The owner reported preparation opening Meet without joining. After removing and
re-adding the exact installed app through macOS UI, permission was restored.
The baseline then stopped on prejoin with the microphone on; the app reported
that the microphone state could not be confirmed. A controlled run with Chrome
explicitly foreground joined automatically, muted, and reached the share picker.
That run used an owner-authorized AppleScript solely to activate Chrome and is
not evidence that the app itself handled the foreground transition.

The native prepare action now activates Chrome itself even for an existing room.
A join-only action lets the app accurately report joined/muted while no PPT is
playing, rather than attempting to share hidden OUTPUT. The Prepare button stays
available in that verified waiting state. A regression blocks readiness and
recording until sharing is confirmed; host mute guarding remains active while
joined/preparing. All 33 Node tests and the complete Electron smoke pass, including
the actual UI's enabled retry button and the existing single-OUTPUT invariants.

Installed final 0.3.7 passed two real-Meet checks: the first opened/joined muted;
the second started with the actual room already on its prejoin screen, microphone
on, and Chrome out of the foreground. Only the app's Prepare action was pressed.
The app activated Chrome, muted, joined and reported joined/waiting-for-PPT. No
manual Join, external foreground script, recording or speaker unmute was used in
these final runs. Actual native Meet controls displayed Turn on microphone and
Leave call, and the Prepare retry button remained enabled. Private final screenshots
are 037-final-autojoin-meet.png and 037-final-autojoin-status.png. App End was used
to leave the test runs. Source/installed-ASAR/helper matching, deep/strict signature,
final DMG checksum and Remote ZIP integrity checks pass.

The owner separately reported window sharing working on this Mac, explicitly
not yet the main laptop. This is owner-reported acceptance, not an independent
receiving-client check. Main-laptop sharing, continuous new-lifecycle capture on
an independent receiver, physical two-Mac/hotspot/audio and scheduled live-service
acceptance remain pending. Ad-hoc update permission persistence is still a known
packaging limitation, not fixed by the join change.


Cleanup qualification: the second End attempt reported "Meet did not accept the
operation" even though the actual Chrome UI subsequently showed the Meet home
page, with no active call. The first End attempt had returned ended. Therefore
this run establishes automatic join/mute, not reliable End completion reporting.
The inconsistent End acknowledgement remains an explicit follow-up. The app was
returned to a clean idle state after preserving the join screenshots. The final
installer instructions were repacked without changing the tested app executable,
ASAR or native helper.

## Main-laptop locator investigation — unpublished 0.3.8

The owner confirmed the main laptop runs 0.3.7, can share the single OUTPUT and
switch decks, but preparation stops at prejoin. Its exact reported error was
"請在 Chrome 登入 Google 並開啟主日 Meet。" despite the visible signed-in room.
This identifies a failed native window lookup, not evidence of missing login.

The lookup previously traversed up to 2,500 nodes per unrelated browser window
before checking the next window, consuming the shared operation deadline. The
candidate searches browser structure breadth-first, reads each web area's URL
without descending into page contents, rejects unrelated/other-room URLs, checks
all Chrome application processes, and raises the matched window. It requests
native accessibility once per Chrome PID after a successful request, retaining
retry eligibility after a failed request. Diagnostics distinguish no web tree,
missing room and lookup timeout; they contain counts, not private page URLs.

The exact Swift locator passes synthetic counterexamples with a 5,000-node
unrelated page, a later matching window, wrong rooms and embedded impersonating
nodes. All 34 Node tests and the complete Electron smoke pass. Both native
architectures compile; the local installed runtime and helper matched that warm
0.3.8 candidate and its deep/strict signature verified.

Importantly, installed 0.3.7 ALSO joined successfully with the large unrelated
window open on this development Mac. Therefore that defect is not a proven
explanation for the main-laptop failure. Installed 0.3.8 opened the real room,
muted microphone/camera and joined using only App Prepare; actual Meet controls
showed "開啟麥克風", "開啟攝像頭" and "退出通話". Private evidence is
038-warm-autojoin-meet.png. This was a warm-Chrome check, not cold-start proof or
main-laptop acceptance. Updating the local test app again invalidated its ad-hoc
Accessibility grant; the same exact app was re-added via normal System Settings
UI, without broadening permission or weakening the signature requirement.

End again actually left this client (Meet showed "你已退出会议") but the App
reported "Meet 未接受操作". Reliable End acknowledgement remains unresolved.
The test app was quit, the owned large-page window closed and its local server
stopped. No recording or speaker unmute was attempted. A full Chrome quit/reopen
test remains pending owner consent because it would close all development-Mac
browser windows. At that checkpoint 0.3.8 was not published.

## Cold-start reproduction and recovery — final 0.3.8

The owner authorized quitting the development Mac's Chrome. At test start the
ordinary GUI Chrome was already gone; the remaining Chrome PID belonged to a
headless website worker with a separate profile. It was left running. The first
candidate failed with window count 0 and web-area count 0: NSWorkspace reused
the running headless instance instead of opening a GUI. Preparation now requests
a new application instance when the requested Meet page is absent, and activates
only the PID belonging to the verified meeting window. This uses
NSWorkspace.OpenConfiguration.createsNewApplicationInstance; Apple's documented
default otherwise reuses an existing instance:
https://developer.apple.com/documentation/appkit/nsworkspace/openconfiguration/createsnewapplicationinstance

That launch correction alone created a GUI but still failed cold: two windows,
zero web areas. Chromium defers enhanced accessibility until two seconds after
the last request. Its setter handles the request before delegating to AppKit,
so the AX return code cannot be treated as a success acknowledgement. The
success-only cache in the earlier candidate and unconditional polling in 0.3.7
could keep requesting every 500ms and resetting that delay. The final helper
reads the application role and sends the enhanced request once per PID,
independent of the AX setter return code. Chromium source authority:
https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/chrome_browser_application_mac.mm
(enableScreenReaderCompleteModeAfterDelay and accessibilitySetValue).

The failed GUI Chrome was normally quit through Activity Monitor's Quit action,
selected by its verified PID; Force Quit was not used. Activity Monitor's original
GPU Processes view and empty search were restored. The headless worker was not
quit or changed. After installing the final candidate and restoring the same App
grant through normal UI, only Presenter Prepare was pressed. Chrome was not
inspected through native/browser tools before the App reported joined/muted.
The App itself started a fresh GUI Chrome, found the room, joined, muted its
microphone and camera, and stayed waiting for PPT sharing. Subsequent actual Meet
inspection showed "開啟麥克風", "開啟攝像頭", "退出通話" and the explicit
camera/microphone-off message. Private proof: 038-cold-autojoin-meet.png and
038-cold-autojoin-status.png. The independent-receiver, main-laptop and full
service/audio checks remain pending; this establishes development-Mac cold start.

All 34 Node tests pass after the final native changes, the installed runtime/helper
match the final build, and its deep/strict signature verifies. The earlier complete
Electron smoke remains applicable to unchanged JavaScript. Both native targets
compile. App End visibly left this test client and returned ended (with an
"操作已取消" suffix); previous inconsistent End acknowledgements remain recorded
and are not cleared by one successful attempt. No recording or speaker unmute was
performed. The update still has the known ad-hoc signing/TCC persistence limitation.

Final DMG checksum and Remote ZIP integrity checks pass. The verified final 0.3.8
pilot artifacts were published through scripts/publish-sunday-presenter.cjs;
latest.json now selects 0.3.8. Presenter DMG: 131544108 bytes,
SHA256 aa14483f181134a5d0db0c1f993fd7e4abc9df2dc6e0bd2ce8c17754bebbeaed.
Remote ZIP: 464105 bytes,
SHA256 8b4251f4f167da558af83fa3ac5bc775e305ce5197db57470600807ffc7622b1.

## Mac/iPad release alignment — 0.6.2 (2026-11-05)

The owner requested synchronizing the Mac counterpart with iPad and keeping all
versions equal. Presenter, universal Mac Remote, signed iPad device bundle and
simulator bundle now have both version fields set to 0.6.2. The single authority
is package.json; the generated Xcode project no longer carries a separate 0.1.0.

Mac keeps its existing window/UI and now supports observer mode and per-host
pair storage through the same production PairStore/RemoteScope used on iPad.
It preserves the old Mac single-host item only for that exact fingerprint;
forget/revocation does not erase another host. The Mac network session uses the
shared immutable endpoint/certificate delegate, rejects redirects and cancels
old sessions on host changes. The existing destroyed-view close/quit repair is
included in the Presenter release.

Validation: 56 Node regression tests pass. They compile and exercise the actual
Mac button/keyboard command path, host switching and late responses; preserve
multiple in-memory host pairs without writing test credentials to the user's
Keychain; exercise legacy-pair decoding; and use the built Mac executable with
real local TLS to verify correct pin acceptance, wrong-pin rejection and denied
redirects. The shared scope suite has 42 checks. Mac builds both arm64/x86_64;
Xcode 26.6 builds the simulator and signs the physical-device architecture using
the existing owner team. Device bundle deep/strict signature verification passes.
Builds use explicit DEVELOPER_DIR and SSD DerivedData; global CLT selection is
unchanged. Website tsc was attempted; this unchanged frontend checkout has its
existing missing cytoscape/react-quill-new dependencies, not a native App error.

At this checkpoint the physical iPad is paired but disconnected in devicectl.
Device installation and post-update pairing/video retention are not inferred
from a successful signed build. The existing #655 automatic-renewal hold remains;
its frozen source snapshot must be intentionally brought to the accepted release
before renewal is re-enabled. No renewal schedule, Meet session, microphone,
recording, website deployment or download publication was started.

Installation follow-up: the paired iPad became reachable through a direct
lock-state query. It was passcode-locked and unlocked since boot. The signed
0.6.2 bundle was installed over the existing app, without uninstalling it;
devicectl readback confirms both version and bundleVersion are 0.6.2. Bundle ID,
owner team and target-device profile binding were checked before installation.
The app was not launched while locked, so post-update pairing/video retention
remains unverified. The automatic-renewal hold is unchanged.

The idle installed Mac Presenter and both existing Mac Remote paths
(/Applications and ~/Applications) were updated to 0.6.2. Deep/strict signatures,
both plist version fields, matching Remote executable SHAs, installed Presenter
stage.cjs equality and the installed Remote's actual control-gate probe pass.
Previous app bundles were retained as rollback ZIPs on SSD, not extra launchable
App copies. Release/install receipts are under the private local test artifact
folder, not a public download. No website release was published.

## Coordinated Mac/iPad deployment — 0.6.3 (2026-11-05)

The owner requested the slideshow-end fix on iPad and the current Mac version
on the church website. The 0.6.2 Mac/iPad pilot is the release base; publishing
0.4.1 alone did not update that pilot. Presenter now includes the exact reviewed
end-notice protection from Phase 1 PR #663. The iPad sends slide commands to
this same protected host page; it does not open or dismiss a separate Office
slideshow. All three products keep the common 0.6.3 version authority.

All 56 Node regressions, the 42 production Swift scope/redirect checks, and
the full Electron smoke pass. Smoke includes an opaque-origin slideshow frame,
extra advances/clicks at the end, backward navigation, invisible end-text
counterexamples, and the previous switching/navigation/crash protections.
The prior real Office end-of-show verification is recorded under #598 / PR #663.

The new iPad bundle was signed with the existing team and application identity;
its profile includes the existing target iPad. Signature verification passed.
It was installed over the existing app without uninstalling; physical-device
readback confirms both version fields are 0.6.3. The device was passcode-locked,
so launch and post-update pairing/video acceptance are not inferred from that
readback. No signing identity, account or developer permissions were added.
The #655 automatic-renewal hold remains unchanged.

Final 0.6.3 deployment readback: the exact packaged Mac app played the real
29-slide OneDrive sermon to its end. Extra native Right, Space, Enter and canvas
click stayed on the same OUTPUT; Left returned to slide 29. No recording or
microphone was enabled. The idle installed Mac Presenter and both existing Mac
Remote copies were updated to 0.6.3, with prior bundles saved as rollback ZIPs
on SSD. All installed version fields and deep/strict signatures verify.

The owner-authorized website publication selects 0.6.3 with pilot status.
The authenticated production admin page displays v0.6.3; an actual browser
download returned Sunday-Presenter-0.6.3-mac-arm64.dmg. Its SHA256 equals the
published manifest (05e478eb996fbe089b331b4f6a993a2fc66e204e4a9681b3809af2516aa55c9e).
Remote ZIP SHA256 is 3b9d230d38cf165ad318438073569a32b60b863666d615b872cd3fa9404f92dd.
Both published file sizes and hashes were independently read back. No website
code changed or website service restart was needed. Private receipts and
screenshots are on SSD under SundayPresenterTests/releases/0.6.3-89402585.
The code artifact is bound to commit 89402585; this subsequent documentation
update does not change packaged code. PR #653 remains stacked on #644; publication
was expressly requested and does not imply completion of the remaining physical
service/hotspot/renewal acceptance gates.


## 2026-11-07 — 0.6.10 slide-only OUTPUT regression fix (#652 / PR #653)

The owner supplied TV and Meet photos showing a colored frame, the PowerPoint
reader status bar, and later the PowerPoint logo/account. Their Meet picker
explicitly selected Sunday Presenter — OUTPUT. The worship deck is 13 1/3 ×
7.5 inches (16:9); the speaker deck is 10 × 7.5 inches (4:3). Only the latter
requires pillarboxing on a 16:9 surface.

The 0.6.7 embedview default did not provide a true slide-only output: it retained
a status bar and lacked the normal Start Slide Show entry. 0.6.10 uses the
read-only preparation page and an explicit first-click action which requests
browser-native fullscreen on Office's live SlidePanel. This does not edit the
PPT, crop it into screenshots, inject slide layout CSS or open a coauthoring
editor. The audience guard reads the actual frame's fullscreen element before
arming or taking an Office preparation page. Exiting fullscreen/navigation
revokes readiness. A stalled frame action has a bounded three-second timeout.

Preparation also had a real viewport mismatch: on this Mac's 1920×1080 primary
LG ULTRAFINE display, macOS limited the preparation window to 971 logical pixels
high while its child view remained 1080 pixels high. Native screenshots were
3840×1942 pixels. Matching actual output bounds with larger-than-screen enabled
restored a 3840×2160 preparation surface. Re-preparing the same preview keeps
its live view attached rather than reparenting it twice.

Real Office validation used the owner's saved 2026-11-08 worship and speaker
files. Both entered slide-only fullscreen, armed and played. Native arrow input
advanced each from slide 1 to slide 2. Switching worship → sermon → worship →
sermon retained both slide-2 positions and fullscreen; saved OUTPUT screenshots
showed no PowerPoint logo, account or status bar, and preserved content/aspect.
The private evidence directory is ~/.local/share/smart-answer/remote-652-upgrade/.
This local observation is not a new independent Meet-receiver or on-site TV
acceptance claim. The clean final 0.6.10 packages were published through the authenticated website;
the DMG downloaded there matched the release SHA256 (131250022 bytes). The app
installed under /Applications also passed the actual Office fullscreen audience
check. Existing host pairing identity remained unchanged. Code artifact:
7142c681; this documentation update does not change packaged code. The previous
0.6.7 app bundle is preserved privately for rollback. iPad remains installed at
0.6.9; this is a Mac update.
