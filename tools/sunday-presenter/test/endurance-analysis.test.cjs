const test=require('node:test');const assert=require('node:assert/strict');
const {addPacket,evaluate}=require('./inspect-recording.cjs');
const packet=(index,pts,duration,dts=pts)=>({stream_index:String(index),pts_time:String(pts),dts_time:String(dts),duration_time:String(duration),size:'100'});
test('packet inspection measures actual timestamp gaps and backwards DTS',()=>{
 const tracks={};addPacket(tracks,packet(0,0,0.02));addPacket(tracks,packet(0,0.02,0.02));assert.equal(tracks[0].maxGap,0);
 addPacket(tracks,packet(0,1,0.02));assert.ok(tracks[0].maxGap>0.9);addPacket(tracks,packet(0,0.5,0.02));assert.equal(tracks[0].backwards,1);
});
test('short or drifting audio fails even when the overall video has enough duration',()=>{
 const streams=[{index:0,type:'audio',codec:'aac',duration:148.5},{index:1,type:'video',codec:'h264',duration:150.3}];
 const tracks={0:{packets:10,backwards:0,maxGap:0,unknownDurations:0},1:{packets:10,backwards:0,maxGap:0,unknownDurations:0}};
 assert.ok(evaluate(streams,tracks,149).includes('Recording shorter than required'));
 assert.ok(evaluate(streams,tracks,149).includes('Audio/video duration difference exceeds 350 ms'));
 streams[0].duration=150.28;assert.deepEqual(evaluate(streams,tracks,149),[]);
 tracks[0].maxGap=0.2;assert.ok(evaluate(streams,tracks,149).includes('Timestamp gap: audio'));
});
