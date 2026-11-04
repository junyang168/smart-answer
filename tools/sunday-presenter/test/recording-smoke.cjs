const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async (fn, label) => { const end = Date.now() + 20000; while (Date.now() < end) { if (await fn()) return; await sleep(50); } throw Error('Recording timeout: ' + label); };
exports.run = async ({ hostVideo, stage, act, output, worship, sermon, evidence }) => {
  const recorder = hostVideo.recording;
  const directory = path.join(evidence, 'recording-' + Date.now()); fs.mkdirSync(directory, { recursive: true });
  const target = path.join(directory, 'PPT-audio-sync.mp4');
  await act('take', { deck: 'sermon' }); await hostVideo.open();
  recorder.permission = async () => {}; // Fixture never opens a physical microphone.
  await hostVideo.window.webContents.executeJavaScript(`(() => {
    navigator.mediaDevices.enumerateDevices = async () => [{ kind:'audioinput', deviceId:'fixture-mic', label:'Synthetic test tone (not physical mic)' }];
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext({ sampleRate: 48000 }); await context.resume();
      const oscillator = context.createOscillator(), gain = context.createGain(), destination = context.createMediaStreamDestination();
      oscillator.frequency.value = 440; gain.gain.value = 0.15; oscillator.connect(gain).connect(destination); oscillator.start();
      window.testTone = oscillator;
      const track = destination.stream.getAudioTracks()[0], originalStop = track.stop.bind(track);
      track.getSettings = () => ({deviceId:'fixture-mic',sampleRate:48000,channelCount:2});
      track.stop = () => { originalStop(); oscillator.stop(); context.close(); };
      return destination.stream;
    };
  })()`, true);
  await recorder.devices(); assert.equal(recorder.snapshot().devices[0].id, 'fixture-mic');
  for (const [page, color] of [[sermon, '#d02020'], [worship, '#2030d0']]) await page.view.webContents.executeJavaScript(`document.body.style.background=${JSON.stringify(color)}`);
  await recorder.start({ target, deviceId: 'fixture-mic' });
  assert.equal(recorder.snapshot().status, 'recording');
  const track = await hostVideo.window.webContents.executeJavaScript('capturedOutput.getVideoTracks()[0].id');
  // Remote's offer reuses the same capture track, and its teardown leaves local recording running.
  hostVideo.request({ op: 'start', clientId: 'recording-fixture-remote' });
  await until(async () => (await hostVideo.stats()).tracks?.includes('video'), 'remote shares capture');
  assert.equal(await hostVideo.window.webContents.executeJavaScript('capturedOutput.getVideoTracks()[0].id'), track);
  hostVideo.reset(); await sleep(200);
  assert.equal(await hostVideo.window.webContents.executeJavaScript('capturedOutput.getVideoTracks()[0].id'), track);
  assert.equal(recorder.snapshot().status, 'recording');
  await sleep(2000);
  await recorder.run.file.tail;
  const incremental = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_packets', '-show_entries', 'packet=codec_type', '-of', 'json', recorder.run.file.partial], { encoding:'utf8' }));
  assert.ok(incremental.packets.some(p => p.codec_type === 'video') && incremental.packets.some(p => p.codec_type === 'audio'), 'MP4 must be readable before stop');
  await Promise.all([act('take', { deck: 'worship' }), hostVideo.window.webContents.executeJavaScript('window.testTone.frequency.value = 880; true')]);
  await sleep(2500);
  await act('key', { key: 'Right' });
  await sleep(1000);
  if (process.argv.includes('--recording-long-smoke')) {
    console.log('RECORDING LONG TEST: 120 seconds unattended');
    for (let second = 0; second < 120; second++) { await sleep(1000); assert.equal(recorder.snapshot().status, 'recording'); }
  }
  const result = await recorder.stop(); assert.equal(result.status, 'saved', JSON.stringify(result));
  assert.ok(result.bytes > 10000);
  assert.equal(await hostVideo.window.webContents.executeJavaScript('window.recordingHost.active()'), false);
  assert.equal(await hostVideo.window.webContents.executeJavaScript('!!capturedOutput'), false);
  const probe = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', target], { encoding: 'utf8' }));
  const video = probe.streams.find(s => s.codec_type === 'video'), audio = probe.streams.find(s => s.codec_type === 'audio');
  assert.equal(probe.streams.length, 2); assert.equal(video.codec_name, 'h264'); assert.equal(audio.codec_name, 'aac');
  assert.ok(+video.duration >= 5); assert.ok(Math.abs(+video.duration - +audio.duration) < 0.3, JSON.stringify(probe.streams));
  execFileSync('ffmpeg', ['-v', 'error', '-i', target, '-f', 'null', '-'], { stdio: 'pipe' });
  const rgb = execFileSync('ffmpeg', ['-v', 'error', '-i', target, '-an', '-vf', 'crop=2:2:iw*0.75:ih*0.75,scale=1:1,fps=4', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], { maxBuffer: 1024 * 1024 });
  let videoTransition;
  for (let i = 0; i + 2 < rgb.length; i += 3) if (rgb[i + 2] > rgb[i] + 50) { videoTransition = i / 3 / 4; break; }
  assert.ok(videoTransition > 1, 'recording must contain the switch to blue worship PPT');
  const pcm = execFileSync('ffmpeg', ['-v', 'error', '-i', target, '-vn', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'], { maxBuffer: 32 * 1024 * 1024 });
  let audioTransition, sum = 0;
  for (let offset = 0; offset + 2400 * 4 <= pcm.length; offset += 2400 * 4) {
    let crossings = 0;
    for (let n = 1; n < 2400; n++) { const sample = pcm.readFloatLE(offset + n * 4); sum += sample * sample;
      if (sample >= 0 && pcm.readFloatLE(offset + (n - 1) * 4) < 0) crossings++; }
    if (crossings / 0.05 > 700 && audioTransition === undefined) audioTransition = offset / 4 / 48000;
  }
  assert.ok(sum > 10, 'AAC must contain decoded non-silent audio');
  assert.ok(Math.abs(audioTransition - videoTransition) < 0.5, JSON.stringify({ audioTransition, videoTransition }));
  // Source invalidation ends and finalizes; a new recording can then start.
  const privacyTarget = path.join(directory, 'auto-stop.mp4');
  await recorder.start({ target: privacyTarget, deviceId: 'fixture-mic' }); await sleep(1500);
  await act('prepare', { id: worship.id });
  await until(() => recorder.snapshot().status === 'saved', 'privacy stop finalizes recording');
  assert.equal(await hostVideo.window.webContents.executeJavaScript('window.recordingHost.active()'), false);
  execFileSync('ffmpeg', ['-v', 'error', '-i', privacyTarget, '-f', 'null', '-'], { stdio: 'pipe' });
  await act('arm', { id: worship.id }); await act('take', { deck: 'worship' });
  const unplugTarget = path.join(directory, 'microphone-disconnected.mp4');
  await recorder.start({ target: unplugTarget, deviceId: 'fixture-mic' }); await sleep(1500);
  await hostVideo.window.webContents.executeJavaScript("recording.microphone.getAudioTracks()[0].dispatchEvent(new Event('ended')); true");
  await until(() => recorder.snapshot().status === 'saved', 'microphone disconnection saves the existing recording');
  assert.match(recorder.snapshot().stopReason, /麥克風/);
  assert.equal(await hostVideo.window.webContents.executeJavaScript('window.recordingHost.active()'), false);
  execFileSync('ffmpeg', ['-v', 'error', '-i', unplugTarget, '-f', 'null', '-'], { stdio: 'pipe' });
  const evidenceResult = { date: new Date().toISOString(), target, privacyTarget, unplugTarget, microphoneLossAutoFinalizes: true, syntheticAudio: true, actualOutputCapture: true, incrementalMP4ReadableBeforeStop: true,
    codec: { video: video.codec_name, audio: audio.codec_name }, width: video.width, height: video.height,
    duration: +video.duration, audioDuration: +audio.duration, videoTransition, audioTransition,
    sharedCaptureWithRemote: true, remoteStopPreservesRecording: true, sourcePrivacyAutoFinalizes: true, allTracksReleased: true };
  fs.writeFileSync(path.join(evidence, 'recording-result.json'), JSON.stringify(evidenceResult, null, 2));
  console.log('RECORDING SMOKE PASS', evidenceResult);
};
