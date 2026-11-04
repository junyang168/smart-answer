const test=require('node:test');const assert=require('node:assert/strict');
const {addPacket,evaluate,addAudioSample,finishZeroRun,signalFindings}=require('./inspect-recording.cjs');
const packet=(index,pts,duration,dts=pts)=>({stream_index:String(index),pts_time:String(pts),dts_time:String(dts),duration_time:String(duration),size:'100'});
test('packet inspection measures actual timestamp gaps and backwards DTS',()=>{
 const tracks={};addPacket(tracks,packet(0,0,0.02));addPacket(tracks,packet(0,0.02,0.02));assert.equal(tracks[0].maxGap,0);
 addPacket(tracks,packet(0,1,0.02));assert.ok(tracks[0].maxGap>0.9);addPacket(tracks,packet(0,0.5,0.02));assert.equal(tracks[0].backwards,1);
});
test('continuous timestamps cannot clear decoded audio silence, including trailing silence',()=>{
 const signal={samples:0,fullScaleSamples:0,zeroRun:0,longestZeroRun:0,digitalSilence:[]};
 const feed=(value,count)=>{for(let i=0;i<count;i++)addAudioSample(signal,value,8000);};
 feed(.01,8000);feed(0,1026);feed(.01,1);
 assert.deepEqual(signal.digitalSilence,[{startSeconds:1,seconds:0.12825}]);
 assert.equal(signalFindings(signal).length,1);
 feed(0,1000);finishZeroRun(signal,8000);
 assert.equal(signal.digitalSilence[1].startSeconds,9027/8000);
 assert.equal(signal.digitalSilence[1].seconds,.125);
});
test('brief zero crossings and AAC overshoot are measured without asserting a source defect',()=>{
 const signal={samples:0,fullScaleSamples:0,zeroRun:0,longestZeroRun:0,digitalSilence:[]};
 for(const v of [.001,0,-.001,0,1.1,.001])addAudioSample(signal,v,8000);
 finishZeroRun(signal,8000);assert.deepEqual(signalFindings(signal),[]);
 assert.equal(signal.fullScaleSamples,1);assert.equal(signal.longestZeroRun,1);
});
test('short or drifting audio fails even when the overall video has enough duration',()=>{
 const streams=[{index:0,type:'audio',codec:'aac',duration:148.5},{index:1,type:'video',codec:'h264',duration:150.3}];
 const tracks={0:{packets:10,backwards:0,maxGap:0,unknownDurations:0},1:{packets:10,backwards:0,maxGap:0,unknownDurations:0}};
 assert.ok(evaluate(streams,tracks,149).includes('Recording shorter than required'));
 assert.ok(evaluate(streams,tracks,149).includes('Audio/video duration difference exceeds 350 ms'));
 streams[0].duration=150.28;assert.deepEqual(evaluate(streams,tracks,149),[]);
 tracks[0].maxGap=0.2;assert.ok(evaluate(streams,tracks,149).includes('Timestamp gap: audio'));
});
