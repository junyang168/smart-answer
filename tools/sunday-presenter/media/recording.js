let recording, lastFailureStats;
function recordingStats(item = recording) {
  if (!item) return lastFailureStats || { active: false };
  const mic = item.microphone?.getAudioTracks()[0];
  return { active: true, draws: item.draws || 0, chunks: item.sequence,
    lastChunkAgeMs: performance.now() - (item.lastChunk || performance.now()), pendingBytes: item.pending,
    recorderState: item.recorder?.state, audioContextState: item.audioContext?.state,
    audioContextSeconds: item.audioContext?.currentTime, normalizedState: item.normalizedAudio?.readyState, microphoneState: mic?.readyState, microphoneMuted: mic?.muted,
    canvasState: item.canvasTrack?.readyState, sourceState: item.outputTrack?.readyState, sourceMuted: item.outputTrack?.muted,
    videoPaused: item.videoElement?.paused, videoReadyState: item.videoElement?.readyState,
    videoSeconds: item.videoElement?.currentTime, videoFrames: item.videoElement?.getVideoPlaybackQuality().totalVideoFrames,
    documentVisibility: document.visibilityState };
}
const recordingError = error => error?.message || String(error);
const releaseTracks = item => {
  clearInterval(item.meter); clearInterval(item.drawTimer);
  item.canvasTrack?.stop(); item.normalizedAudio?.stop();
  if (item.videoElement) { item.videoElement.pause(); item.videoElement.srcObject = null; }
  item.microphone?.getTracks().forEach(track => track.stop());
  item.audioContext?.close().catch(() => {});
  window.outputCapture.release('recording');
};
function failRecording(item, error) {
  if (recording !== item || item.stopping) return;
  item.error ||= recordingError(error); lastFailureStats = recordingStats(item);
  window.recordingBridge.fault(item.id, item.error);
}
function stopEarly(item, message) {
  if (recording !== item || item.stopping) return;
  item.stopReason ||= message; window.recordingBridge.fault(item.id, message, true);
}
window.addEventListener('output-capture-ended', () => { if (recording) stopEarly(recording, 'PPT 畫面擷取已停止，錄影已儲存。'); });
window.recordingHost = {
  stats: recordingStats,
  async devices() {
    const devices = (await navigator.mediaDevices.enumerateDevices())
      .filter(d => d.kind === 'audioinput' && !['default', 'communications'].includes(d.deviceId));
    return devices.map((d, index) => ({ id: d.deviceId, label: d.label || `麥克風 ${index + 1}` }));
  },
  async start({ id, deviceId }) {
    if (recording) throw Error('已在錄影。');
    const mimeType = 'video/mp4;codecs=avc1,mp4a.40.2';
    if (!MediaRecorder.isTypeSupported(mimeType)) throw Error('這台 Mac 不支援 H.264／AAC MP4 錄影。');
    const item = { id, pending: 0, sequence: 0, writes: Promise.resolve(), oldest: 0 }; recording = item;
    try {
      item.microphone = await navigator.mediaDevices.getUserMedia({ video: false, audio: {
        deviceId: { exact: deviceId }, echoCancellation: false, noiseSuppression: false, autoGainControl: false
      } });
      if (recording !== item || item.stopping) throw Error('錄影已取消。');
      const mic = item.microphone.getAudioTracks()[0];
      if (!mic || mic.getSettings().deviceId !== deviceId) throw Error('無法使用選定的麥克風；不會自動改用其他裝置。');
      mic.addEventListener('ended', () => stopEarly(item, '選定的麥克風已中斷，錄影已停止並儲存。'));
      // Drive the encoder from the Web Audio render clock rather than the USB
      // source's push timing. Actual AudioPro direct-track recording lost about
      // 1.8 seconds of audio in 150 seconds; destination output is continuous.
      item.audioContext = new AudioContext({ sampleRate: 48000 });
      item.audioSource = item.audioContext.createMediaStreamSource(item.microphone);
      item.audioDestination = item.audioContext.createMediaStreamDestination();
      item.audioDestination.channelCount = Math.min(2, mic.getSettings().channelCount || 1);
      item.analyser = item.audioContext.createAnalyser(); item.analyser.fftSize = 1024;
      item.audioSource.connect(item.audioDestination); item.audioSource.connect(item.analyser);
      item.normalizedAudio = item.audioDestination.stream.getAudioTracks()[0];
      await item.audioContext.resume();
      const video = await window.outputCapture.acquire('recording'); item.outputTrack = video.getVideoTracks()[0];
      if (recording !== item || item.stopping) throw Error('錄影已取消。');
      // ScreenCaptureKit can omit unchanged frames. Sample the SAME captured
      // OUTPUT into a recording-only canvas so a still slide has a continuous
      // video clock and its final MP4 duration follows microphone duration.
      item.videoElement = document.createElement('video');
      item.videoElement.muted = true; item.videoElement.srcObject = new MediaStream(video.getVideoTracks());
      await item.videoElement.play();
      const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 720;
      const context = canvas.getContext('2d', { alpha: false });
      const frames = canvas.captureStream(0); item.canvasTrack = frames.getVideoTracks()[0];
      const draw = () => {
        item.draws = (item.draws || 0) + 1;
        const width = item.videoElement.videoWidth, height = item.videoElement.videoHeight;
        if (!width || !height) return;
        const scale = Math.min(canvas.width / width, canvas.height / height);
        context.fillStyle = '#000'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(item.videoElement, (canvas.width - width * scale) / 2, (canvas.height - height * scale) / 2, width * scale, height * scale);
        item.canvasTrack.requestFrame();
      };
      draw(); item.drawTimer = setInterval(draw, 1000 / 30);
      const stream = new MediaStream([item.canvasTrack, item.normalizedAudio]);
      item.recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 4000000, audioBitsPerSecond: 192000, videoKeyFrameIntervalDuration: 500 });
      item.finished = new Promise(resolve => { item.recorder.onstop = resolve; });
      item.recorder.onerror = event => failRecording(item, event.error || Error('影音編碼失敗。'));
      item.recorder.ondataavailable = event => {
        if (!event.data.size || item.error) return;
        item.lastChunk = performance.now();
        item.pending += event.data.size;
        if (item.pending > 16 * 1024 * 1024) { failRecording(item, Error('錄影寫入排隊超過 16 MiB。')); return; }
        const sequence = item.sequence++;
        item.oldest ||= performance.now();
        item.writes = item.writes.then(async () => {
          const raw = await event.data.arrayBuffer();
          await window.recordingBridge.chunk(id, sequence, raw);
          item.pending -= event.data.size;
          if (!item.pending) item.oldest = 0;
        }).catch(error => { item.error ||= recordingError(error); window.recordingBridge.fault(id, item.error); });
      };
      const analyser = item.analyser;
      const samples = new Float32Array(analyser.fftSize);
      item.meter = setInterval(() => {
        if (performance.now() - item.lastChunk > 5000) { failRecording(item, Error('影音編碼超過五秒沒有輸出，錄影已停止。')); return; }
        if (item.oldest && performance.now() - item.oldest > 2000) { failRecording(item, Error('錄影寫入排隊超過兩秒。')); return; }
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
        window.recordingBridge.level(id, rms);
      }, 500);
      item.lastChunk = performance.now();
      item.recorder.start(500);
      return { mimeType: item.recorder.mimeType, microphone: mic.label, audio: mic.getSettings(), video: video.getVideoTracks()[0].getSettings() };
    } catch (error) {
      recording = null; releaseTracks(item); throw error;
    }
  },
  async stop() {
    const item = recording;
    if (!item) return {};
    if (item.stopPromise) return item.stopPromise;
    item.stopping = true;
    item.stopPromise = (async () => {
      // Stop microphone capture immediately; keep only encoder finalization.
      clearInterval(item.meter);
      if (item.recorder && item.recorder.state !== 'inactive') item.recorder.stop();
      item.microphone?.getTracks().forEach(track => track.stop());
      if (item.recorder) await item.finished;
      await item.writes;
      releaseTracks(item); if (recording === item) recording = null;
      return { error: item.error || '', chunks: item.sequence };
    })();
    return item.stopPromise;
  },
  active() { return !!recording; }
};
