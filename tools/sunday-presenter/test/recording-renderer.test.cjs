const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function fixture(deviceId = 'usb-selected') {
  const connections = [], faults = [], timers = new Set(), releases = [];
  const track = (id, kind) => ({ id, kind, readyState:'live', label:id, callbacks:{},
    getSettings() { return {deviceId:id,channelCount:2,width:1280,height:720}; },
    addEventListener(name, fn) { this.callbacks[name]=fn; },
    stop() { this.readyState='ended'; }, requestFrame() {} });
  const mic=track(deviceId,'audio'), normalized=track('normalized','audio'), video=track('output','video'), canvas=track('canvas','video');
  class Stream { constructor(tracks) { this.tracks=tracks; } getTracks(){return this.tracks;} getAudioTracks(){return this.tracks.filter(t=>t.kind==='audio');} getVideoTracks(){return this.tracks.filter(t=>t.kind==='video');} }
  const contexts=[], recorders=[];
  class Context {
    constructor(options) { this.options=options; this.state='suspended'; this.currentTime=0; contexts.push(this); }
    createMediaStreamSource(stream) { assert.equal(stream.getAudioTracks()[0],mic); return {connect:node=>connections.push(node.kind)}; }
    createMediaStreamDestination() { return {kind:'stream',stream:new Stream([normalized])}; }
    createAnalyser() { return {kind:'meter',fftSize:1024,getFloatTimeDomainData(){}}; }
    async resume(){this.state='running';} async close(){this.state='closed';}
  }
  class Recorder {
    static isTypeSupported(){return true;}
    constructor(stream,options){this.stream=stream;this.options=options;this.state='inactive';this.mimeType=options.mimeType;recorders.push(this);}
    start(){this.state='recording';} stop(){this.state='inactive';queueMicrotask(()=>this.onstop());}
  }
  const window={addEventListener(){},outputCapture:{acquire:async()=>new Stream([video]),release:owner=>releases.push(owner)},
    recordingBridge:{fault:(...args)=>faults.push(args),level(){},chunk:async()=>{}}};
  const document={visibilityState:'visible',createElement:type=>type==='video'?{videoWidth:1280,videoHeight:720,play:async()=>{},pause(){},getVideoPlaybackQuality:()=>({totalVideoFrames:1})}:
    {getContext:()=>({fillRect(){},drawImage(){}}),captureStream:()=>new Stream([canvas])}};
  let constraints;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../media/recording.js'),'utf8'),{window,document,MediaStream:Stream,MediaRecorder:Recorder,AudioContext:Context,
    navigator:{mediaDevices:{getUserMedia:async value=>{constraints=value;return new Stream([mic]);}}},
    performance:{now:()=>100},setInterval:fn=>{timers.add(fn);return fn;},clearInterval:fn=>timers.delete(fn),Float32Array});
  return {api:window.recordingHost,mic,normalized,canvas,recorders,contexts,connections,faults,timers,releases,get constraints(){return constraints;}};
}
test('selected USB input is clock-normalized for encoding without routing to speakers',async()=>{
  const f=fixture();await f.api.start({id:'run',deviceId:'usb-selected'});
  assert.equal(f.constraints.audio.deviceId.exact,'usb-selected');assert.equal(f.constraints.video,false);
  assert.equal(f.contexts[0].options.sampleRate,48000);
  assert.deepEqual(f.connections,['stream','meter']);
  assert.equal(f.recorders[0].stream.getAudioTracks()[0],f.normalized);
  assert.equal(f.recorders[0].stream.getVideoTracks()[0],f.canvas);
  await f.api.stop();assert.equal(f.mic.readyState,'ended');assert.equal(f.normalized.readyState,'ended');
  assert.equal(f.contexts[0].state,'closed');assert.equal(f.timers.size,0);assert.deepEqual(f.releases,['recording']);
});
test('a different physical device is rejected before opening output capture',async()=>{
  const f=fixture('other-device');await assert.rejects(f.api.start({id:'run',deviceId:'usb-selected'}),/選定/);
  assert.equal(f.recorders.length,0);assert.equal(f.contexts.length,0);assert.equal(f.mic.readyState,'ended');
});
test('physical microphone loss signals graceful stop even while normalized track is live',async()=>{
  const f=fixture();await f.api.start({id:'run',deviceId:'usb-selected'});f.mic.callbacks.ended();
  assert.equal(f.faults[0][0],'run');assert.equal(f.faults[0][2],true);await f.api.stop();
});
