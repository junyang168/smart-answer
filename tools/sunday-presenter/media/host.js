let current;
async function gathered(pc) {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pc.removeEventListener('icegatheringstatechange', check); reject(Error('ICE timeout')); }, 8000);
    function check() { if (pc.iceGatheringState === 'complete') { clearTimeout(timer); pc.removeEventListener('icegatheringstatechange', check); resolve(); } }
    pc.addEventListener('icegatheringstatechange', check); check();
  });
}
function stop(id) {
  if (!current || (id && current.id !== id)) return;
  const item = current; current = null;
  item.stream?.getTracks().forEach(track => track.stop()); item.pc.close();
}
window.videoBridge.onStop(stop);
window.videoHost = {
  stop,
  async offer(id) {
    stop();
    const item = { id, pc: new RTCPeerConnection({ iceServers: [] }) }; current = item;
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 } }, audio: false });
      if (current !== item) { stream.getTracks().forEach(track => track.stop()); throw Error('Cancelled'); }
      item.stream = stream;
      const track = stream.getVideoTracks()[0];
      track.addEventListener('ended', () => stop(id));
      const transceiver = item.pc.addTransceiver(track, { direction: 'sendonly', streams: [stream] });
      const codecs = RTCRtpSender.getCapabilities('video')?.codecs;
      if (codecs) transceiver.setCodecPreferences([...codecs.filter(c => /h264/i.test(c.mimeType)), ...codecs.filter(c => !/h264/i.test(c.mimeType))]);
      const params = transceiver.sender.getParameters();
      if (params.encodings?.length) { params.encodings[0].maxBitrate = 3000000; await transceiver.sender.setParameters(params); }
      await item.pc.setLocalDescription(await item.pc.createOffer()); await gathered(item.pc);
      if (current !== item) throw Error('Cancelled');
      return item.pc.localDescription.sdp;
    } catch (error) { stop(id); throw error; }
  },
  async answer(id, sdp) {
    if (current?.id !== id) throw Error('Expired');
    await current.pc.setRemoteDescription({ type: 'answer', sdp });
  },
  async stats() {
    if (!current) return { state: 'stopped' };
    const stats = [...(await current.pc.getStats()).values()];
    return { id: current.id, state: current.pc.connectionState, tracks: current.stream?.getTracks().map(t => t.kind),
      outbound: stats.filter(s => s.type === 'outbound-rtp').map(s => ({ kind: s.kind, framesEncoded: s.framesEncoded, bytesSent: s.bytesSent })),
      pairs: stats.filter(s => s.type === 'candidate-pair' && s.nominated).map(s => ({ state: s.state, local: stats.find(c => c.id === s.localCandidateId)?.candidateType, remote: stats.find(c => c.id === s.remoteCandidateId)?.candidateType })) };
  }
};
