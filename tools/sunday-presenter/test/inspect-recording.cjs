const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const readline = require('node:readline');
function addAudioSample(signal, value, sampleRate) {
  signal.samples++;
  if (Math.abs(value) >= 1) signal.fullScaleSamples++;
  if (Math.abs(value) < 1e-8) signal.zeroRun++;
  else finishZeroRun(signal, sampleRate, signal.samples - 1);
}
function finishZeroRun(signal, sampleRate, endSample = signal.samples) {
  if (signal.zeroRun / sampleRate >= 0.1) signal.digitalSilence.push({
    startSeconds: (endSample - signal.zeroRun) / sampleRate,
    seconds: signal.zeroRun / sampleRate });
  signal.longestZeroRun = Math.max(signal.longestZeroRun, signal.zeroRun);
  signal.zeroRun = 0;
}
function signalFindings(signal) {
  return signal.digitalSilence.length ? ['Digital silence >=100 ms requires source/recording investigation; packet continuity alone does not establish uninterrupted sound'] : [];
}
function addPacket(tracks, values) {
  const index=Number(values.stream_index), pts=Number(values.pts_time), dts=Number(values.dts_time), duration=Number(values.duration_time);
  if (!Number.isFinite(index) || !Number.isFinite(pts)) return;
  const item=tracks[index] ||= {packets:0,bytes:0,firstPTS:pts,lastEnd:pts,lastDTS:null,maxGap:0,backwards:0,sumDuration:0,unknownDurations:0};
  item.packets++; item.bytes+=Number(values.size)||0;
  if (item.lastDTS!==null && Number.isFinite(dts) && dts+0.000002<item.lastDTS) item.backwards++;
  if (Number.isFinite(dts)) item.lastDTS=dts;
  if (pts>=item.lastEnd) item.maxGap=Math.max(item.maxGap,pts-item.lastEnd);
  if (!Number.isFinite(duration)) item.unknownDurations++;
  item.sumDuration+=Number.isFinite(duration)?duration:0;
  item.firstPTS=Math.min(item.firstPTS,pts);item.lastEnd=Math.max(item.lastEnd,pts+(Number.isFinite(duration)?duration:0));
}
async function packets(file) {
  const child=spawn('ffprobe',['-v','error','-show_packets','-show_entries','packet=stream_index,pts_time,dts_time,duration_time,size','-of','compact=p=0:nk=0',file]);
  let error='';child.stderr.on('data',data=>{if(error.length<100000)error+=data;});
  const done=new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',code=>code===0?resolve():reject(Error(error||'packet inspection failed')));});
  const tracks={};
  for await(const line of readline.createInterface({input:child.stdout})) {
    const values=Object.fromEntries(line.split('|').map(part=>part.split('=')));
    addPacket(tracks,values);
  }
  await done;return tracks;
}
function evaluate(streams, trackPackets, minimumSeconds) {
  const failures=[];const audio=streams.find(s=>s.type==='audio'),video=streams.find(s=>s.type==='video');
  if(streams.length!==2||audio?.codec!=='aac'||video?.codec!=='h264')failures.push('Unexpected streams/codecs');
  if(!audio||!video||Math.min(audio.duration,video.duration)<minimumSeconds)failures.push('Recording shorter than required');
  if(audio&&video&&Math.abs(audio.duration-video.duration)>0.35)failures.push('Audio/video duration difference exceeds 350 ms');
  for(const stream of streams){const track=trackPackets[stream.index];if(!track?.packets)failures.push('No packets: '+stream.type);else{
    if(track.backwards)failures.push('Backward DTS: '+stream.type);
    if(track.maxGap>(stream.type==='audio'?0.1:0.5))failures.push('Timestamp gap: '+stream.type);
    if(track.unknownDurations)failures.push('Unmeasured packet duration: '+stream.type);
  }}
  return failures;
}
async function inspect(file, directory, minimumSeconds=0) {
  await fsp.mkdir(directory,{recursive:true});const frameDir=path.join(directory,'frames');await fsp.mkdir(frameDir,{recursive:true});
  const probe=JSON.parse(execFileSync('ffprobe',['-v','error','-show_streams','-show_format','-of','json',file],{encoding:'utf8'}));
  const streams=probe.streams.map(s=>({index:s.index,type:s.codec_type,codec:s.codec_name,duration:Number(s.duration),start:Number(s.start_time),width:s.width,height:s.height,sampleRate:s.sample_rate,channels:s.channels}));
  const trackPackets=await packets(file);
  const sampleRate=8000,buckets=[],signal={samples:0,fullScaleSamples:0,zeroRun:0,longestZeroRun:0,digitalSilence:[]};let remainder=Buffer.alloc(0),samples=0,lastProgress=0,errors='';
  const child=spawn('ffmpeg',['-v','error','-xerror','-i',file,'-map','0:v:0','-vf','fps=1/60,scale=480:-2','-q:v','3','-y',path.join(frameDir,'frame-%03d.jpg'),'-map','0:a:0','-ac','1','-ar',String(sampleRate),'-f','f32le','pipe:1','-progress','pipe:3','-nostats'],{stdio:['ignore','pipe','pipe','pipe']});
  child.stderr.on('data',data=>{if(errors.length<1000000)errors+=data;});child.stdio[3].on('data',()=>{});
  const done=new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',code=>code===0?resolve():reject(Error(errors||'full decode failed')));});
  for await(const chunk of child.stdout) {
    const data=remainder.length?Buffer.concat([remainder,chunk]):chunk,length=data.length-data.length%4;
    for(let offset=0;offset<length;offset+=4) {
      const value=data.readFloatLE(offset);if(!Number.isFinite(value))throw Error('Nonfinite decoded audio');
      addAudioSample(signal,value,sampleRate);
      const index=Math.floor(samples/(sampleRate*60));const bucket=buckets[index]||={samples:0,squares:0,peak:0,zeros:0};
      bucket.samples++;bucket.squares+=value*value;bucket.peak=Math.max(bucket.peak,Math.abs(value));if(Math.abs(value)<1e-8)bucket.zeros++;samples++;
    }
    remainder=Buffer.from(data.subarray(length));
    if(Date.now()-lastProgress>10000){lastProgress=Date.now();const progress={stage:'full-decode',decodedAudioSeconds:samples/sampleRate,duration:probe.format.duration};await fsp.writeFile(path.join(directory,'analysis-progress.json'),JSON.stringify(progress));console.log(JSON.stringify(progress));}
  }
  await done;
  // Finish a trailing silence run using the same exclusive-end convention.
  finishZeroRun(signal,sampleRate);
  const mediaFailures=evaluate(streams,trackPackets,minimumSeconds);
  const audioSignalFindings=signalFindings(signal),failures=[...mediaFailures,...audioSignalFindings];
  const audioMinutes=buckets.map((b,index)=>({minute:index,seconds:b.samples/sampleRate,rms:Math.sqrt(b.squares/b.samples),peak:b.peak,zeroFraction:b.zeros/b.samples}));
  const result={file,bytes:(await fsp.stat(file)).size,checkedAt:new Date().toISOString(),minimumSeconds,streams,trackPackets,fullDecodeErrors:errors,audioMinutes,decodedAudioSeconds:samples/sampleRate,
    snapshots:(await fsp.readdir(frameDir)).filter(n=>n.endsWith('.jpg')).length,
    audioSignal:{...signal,sampleRate,longestDigitalZeroRunSeconds:signal.longestZeroRun/sampleRate},
    audioSignalFindings,mediaTimelinePassed:mediaFailures.length===0,signalReviewRequired:audioSignalFindings.length>0,
    failures,passed:failures.length===0};
  await fsp.writeFile(path.join(directory,'analysis.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify({stage:'complete',passed:result.passed,streams,trackPackets,snapshots:result.snapshots,failures}));
  if(failures.length)throw Error(failures.join('; '));return result;
}
if(require.main===module)inspect(process.argv[2],process.argv[3],Number(process.argv[4]||0)).catch(error=>{console.error(error);process.exitCode=1;});
module.exports={inspect,addPacket,evaluate,addAudioSample,finishZeroRun,signalFindings};
