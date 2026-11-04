const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
exports.run = async ({ hostVideo, act, evidence }) => {
  const recorder = hostVideo.recording;
  await act('take', { deck: 'sermon' });
  const devices = await recorder.devices();
  console.log('physical microphone inputs:', devices.map(d => d.label));
  const mic = devices.find(d => /AudioPro X5/i.test(d.label));
  assert.ok(mic, 'Actual AudioPro X5 receiver must be explicitly present; never fall back to another mic.');
  const target = path.join(evidence, `AudioPro-X5-actual-${Date.now()}.mp4`);
  await recorder.start({ target, deviceId: mic.id });
  const durationArg = process.argv.find(arg => arg.startsWith('--recording-hardware-seconds='));
  const seconds = durationArg ? Number(durationArg.split('=')[1]) : 10;
  assert.ok(seconds > 0 && seconds <= 6000);
  console.log(`ACTUAL MICROPHONE RECORDING: ${seconds} seconds, AudioPro X5 only.`);
  let peak = 0;
  for (let i = 0; i < seconds * 2; i++) {
    await new Promise(resolve => setTimeout(resolve, 500)); peak = Math.max(peak, recorder.snapshot().level);
    if (i % 20 === 0 || recorder.snapshot().status !== 'recording') {
      const diagnostics = await hostVideo.window.webContents.executeJavaScript('window.recordingHost.stats()');
      console.log('HARDWARE PROGRESS', JSON.stringify({ seconds: i / 2, status: recorder.snapshot().status, diagnostics }));
      fs.appendFileSync(path.join(evidence,'hardware-diagnostics.jsonl'), JSON.stringify({ seconds:i/2,status:recorder.snapshot().status,diagnostics })+'\n');
    }
    assert.equal(recorder.snapshot().status, 'recording', JSON.stringify(recorder.snapshot()));
  }
  const result = await recorder.stop();
  assert.equal(result.status, 'saved', JSON.stringify(result));
  const probe = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_streams', '-of', 'json', target], { encoding: 'utf8' }));
  assert.equal(probe.streams.find(s => s.codec_type === 'audio')?.codec_name, 'aac');
  assert.equal(probe.streams.find(s => s.codec_type === 'video')?.codec_name, 'h264');
  const audioDuration = Number(probe.streams.find(s => s.codec_type === 'audio').duration);
  const videoDuration = Number(probe.streams.find(s => s.codec_type === 'video').duration);
  assert.ok(Math.abs(audioDuration - videoDuration) < 0.35, JSON.stringify({ audioDuration, videoDuration }));
  execFileSync('ffmpeg', ['-v', 'error', '-i', target, '-f', 'null', '-'], { stdio: 'pipe' });
  const pcm = execFileSync('ffmpeg', ['-v', 'error', '-i', target, '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'], { maxBuffer: 10 * 1024 * 1024 });
  let squares = 0, max = 0;
  for (let offset = 0; offset < pcm.length; offset += 4) { const value = pcm.readFloatLE(offset); squares += value * value; max = Math.max(max, Math.abs(value)); }
  const evidenceResult = { date: new Date().toISOString(), target, microphone: result.microphone, devices: devices.map(d => d.label),
    sampleRate: result.audio.sampleRate, channels: result.audio.channelCount,
    syntheticAudio: false, actualMicrophone: true, meterPeak: peak, decodedPeak: max, decodedRMS: Math.sqrt(squares / (pcm.length / 4)),
    streams: probe.streams.map(s => ({ type: s.codec_type, codec: s.codec_name, duration: s.duration })) };
  fs.writeFileSync(path.join(evidence, 'hardware-recording-result.json'), JSON.stringify(evidenceResult, null, 2));
  console.log('HARDWARE RECORDING PASS', evidenceResult);
};
