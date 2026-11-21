const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {execFileSync}=require('node:child_process');
async function until(check,label){const end=Date.now()+12000;while(Date.now()<end){if(await check())return;await new Promise(r=>setTimeout(r,50));}throw Error('Music smoke timeout: '+label);}
exports.run=async({music,act,stage,controller,setOrigin})=>{
 const {app}=require('electron');const root=path.join(app.getPath('userData'),'music-smoke');fs.mkdirSync(root,{recursive:true});
 const mp3=path.join(root,'Test accompaniment.mp3');
 execFileSync('/opt/homebrew/bin/ffmpeg',['-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=20','-c:a','libmp3lame','-q:a','5','-y',mp3]);
 const server=http.createServer((_req,res)=>{res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>MusicFixture.pptx</title><body style="background:#15263c;color:white"><h1>Lyrics stay on OUTPUT</h1><script>window.token=Math.random();window.steps=0;document.onkeydown=()=>steps++;</script>');});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;setOrigin(origin);
 try{
  for(const deck of ['worship','sermon']){await act('open',{deck,url:origin+'/'+deck});const id=stage.preview;await until(()=>stage.get(id).loaded&&!stage.get(id).view.webContents.isLoading(),'fixture load');await act('arm',{id});}
  await music.assign('hymn',mp3);await music.assign('responseHymn',mp3);await music.assign('doxology',mp3);
  assert.equal(music.snapshot().status,'stopped');assert.equal(music.window,undefined,'no autoplay/window on adding files');
  await music.toggle('hymn');await until(()=>music.snapshot().status==='playing'&&music.snapshot().position>0.5,'MP3 plays');
  const first=music.snapshot().position;
  for(let i=0;i<6;i++){await act('take',{deck:i%2?'worship':'sermon'});}
  await until(()=>music.snapshot().position>first+0.5,'PPT switching preserves continuous music');
  assert.equal(music.snapshot().status,'playing');assert.equal(music.window.webContents.isAudioMuted(),false);
  await music.volume(0.25);assert.equal(await music.window.webContents.executeJavaScript("document.querySelector('audio').volume"),0.25);
  await music.toggle('hymn');await until(()=>music.snapshot().status==='paused','pause');const paused=music.snapshot().position;
  await new Promise(r=>setTimeout(r,300));assert.ok(Math.abs(music.snapshot().position-paused)<0.1);
  await music.toggle('hymn');await until(()=>music.snapshot().status==='playing','resume');assert.ok(music.snapshot().position>=paused);
  await music.toggle('doxology');await until(()=>music.snapshot().status==='playing'&&music.snapshot().activeSlot==='doxology','doxology');
  assert.ok(music.snapshot().position<2,'another slot starts from beginning, including when it uses same file');
  await until(()=>music.snapshot().duration>10,'metadata');await music.seek(8);await until(()=>music.snapshot().position>=8,'seek');
  await music.stop();await until(()=>music.snapshot().status==='stopped'&&music.snapshot().position<0.1,'stop returns to beginning');
  await music.toggle('responseHymn');await until(()=>music.snapshot().status==='playing','response');
  const audioState=await music.window.webContents.executeJavaScript("({count:document.querySelectorAll('audio').length,paused:document.querySelector('audio').paused})");assert.equal(audioState.count,1);
  await music.seek(19.8);await until(()=>music.snapshot().status==='ended','end does not advance');assert.equal(music.snapshot().activeSlot,'responseHymn');
  const html=await controller.webContents.executeJavaScript("document.querySelector('#music-slots').innerText");for(const label of ['詩歌','回應詩歌','三一頌'])assert.ok(html.includes(label));
  const {MusicLibrary}=require('../music-library.cjs');const restored=new MusicLibrary(music.library.file);assert.equal(restored.volume,0.25);assert.deepEqual(restored.slots,music.library.slots);
  await music.stop();music.loaded=null;const pending=music.toggle('hymn');await music.stop();await pending;await new Promise(r=>setTimeout(r,200));assert.notEqual(music.snapshot().status,'playing','stop cancels a pending start');
  const bad=path.join(root,'Invalid.mp3');fs.writeFileSync(bad,'not an mp3');await music.assign('hymn',bad);
  await music.toggle('hymn').catch(()=>{});await until(()=>music.snapshot().status==='error','decode failure visible');
  assert.equal(music.snapshot().activeSlot,'hymn');
  const result={realMp3:true,threeSlots:true,noAutoplay:true,continuousAcrossDecks:true,pauseResume:true,doxology:true,seek:true,stopResets:true,oneAudioElement:true,noAutomaticAdvance:true,volumePersists:true,decodeFailureVisible:true,stopCancelsPendingStart:true,meetReceiver:'NOT TESTED'};
  fs.writeFileSync(path.join(root,'result.json'),JSON.stringify(result,null,2));console.log('MUSIC SMOKE PASS',JSON.stringify(result));
 }finally{await music.stop().catch(()=>{});server.close();}
};
