const video = document.querySelector('video'), message = document.querySelector('#message');
let current;
const notify = value => window.webkit.messageHandlers.video.postMessage(value);
function stop(text = '等待主電腦播放 PPT') {
  const old = current; current = null;
  clearTimeout(old?.disconnectTimer); old?.pc?.close(); video.srcObject = null; document.body.classList.remove('receiving'); message.textContent = text;
}
async function gathered(pc) {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pc.removeEventListener('icegatheringstatechange', check); reject(Error('ICE timeout')); }, 8000);
    function check() { if (pc.iceGatheringState === 'complete') { clearTimeout(timer); pc.removeEventListener('icegatheringstatechange', check); resolve(); } }
    pc.addEventListener('icegatheringstatechange', check); check();
  });
}
window.remoteVideo = {
  stop,
  sample() {
    const canvas = document.createElement('canvas'); canvas.width = 32; canvas.height = 32;
    const ctx = canvas.getContext('2d'); ctx.drawImage(video, 0, 0, 32, 32);
    return { pixel: [...ctx.getImageData(29, 29, 1, 1).data], visible: document.body.classList.contains('receiving'), sessionId: current?.id };
  },
  async apply(value) {
    if (value.status === 'failed') { stop(value.error || '畫面連線失敗'); notify({ op: 'failed', sessionId: value.sessionId, error: value.error || '畫面連線失敗' }); return; }
    if (!value.offer || current?.id === value.sessionId) return;
    stop('正在連接 PPT 畫面…');
    const item = { id: value.sessionId, pc: null, started: performance.now(), receivedFrame: false };
    try {
      item.pc = new RTCPeerConnection({ iceServers: [] }); current = item;
      item.pc.ontrack = event => {
        if (current !== item) return;
        if (event.track.kind !== 'video') { stop('收到不支援的媒體'); return; }
        video.srcObject = new MediaStream([event.track]); video.play().catch(() => {});
      };
      item.pc.onconnectionstatechange = () => {
        if (current !== item) return;
        clearTimeout(item.disconnectTimer);
        const failed = () => {
          if (current !== item) return;
          stop('畫面已斷線，正在重連…'); notify({ op: 'failed', sessionId: item.id });
        };
        if (['failed', 'closed'].includes(item.pc.connectionState)) failed();
        // ICE may recover from a short hotspot interruption without renegotiation.
        else if (item.pc.connectionState === 'disconnected') item.disconnectTimer = setTimeout(failed, 5000);
      };
      await item.pc.setRemoteDescription(value.offer);
      if (item.pc.getTransceivers().length !== 1 || item.pc.getTransceivers()[0].receiver.track.kind !== 'video') throw Error('Video only');
      await item.pc.setLocalDescription(await item.pc.createAnswer()); await gathered(item.pc);
      if (current !== item) return;
      notify({ op: 'answer', sessionId: item.id, sdp: item.pc.localDescription.sdp });
    } catch { if (!current || current === item) { stop('畫面連線未完成，正在重試…'); notify({ op: 'failed', sessionId: item.id }); } }
  }
};
setInterval(async () => {
  const item = current; if (!item) return;
  try {
    const stats = [...(await item.pc.getStats()).values()]; if (current !== item) return;
    const inbound = stats.find(s => s.type === 'inbound-rtp' && (s.kind === 'video' || s.mediaType === 'video'));
    const frames = inbound?.framesDecoded || 0;
    if (frames > 0) { item.receivedFrame = true; document.body.classList.add('receiving'); }
    // A still slide may produce no new frames. ICE and native authenticated
    // polling detect lost transport; frame-count inactivity is not a disconnect.
    if (!item.receivedFrame && performance.now() - item.started > 25000) {
      stop('畫面連線未完成，正在重試…'); notify({ op: 'failed', sessionId: item.id }); return;
    }
    if (video.srcObject && video.paused) video.play().catch(() => {});
    notify({ op: 'stats', sessionId: item.id, frames, width: video.videoWidth, height: video.videoHeight,
      audio: item.pc.getReceivers().some(r => r.track.kind === 'audio'),
      candidates: stats.filter(s => s.type === 'candidate-pair' && s.nominated).map(s => ({ local: stats.find(c => c.id === s.localCandidateId)?.candidateType, remote: stats.find(c => c.id === s.remoteCandidateId)?.candidateType })) });
  } catch { /* Native control polling separately detects a lost host. */ }
}, 1000);
