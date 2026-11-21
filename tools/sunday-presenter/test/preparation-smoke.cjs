const assert = require('node:assert/strict');
const http = require('node:http');
const { comingSunday } = require('../website.cjs');
async function until(check,label){const end=Date.now()+15000;while(Date.now()<end){if(await check())return;await new Promise(r=>setTimeout(r,25));}throw Error('Preparation smoke timeout: '+label);}
exports.run=async({preparer,act,stage,controller,website,officeSession})=>{
 preparer.driver.folderTimeoutMs=15000;
 const date=comingSunday(),folderName=date.split('-').map(Number).slice(1).join('-')+'-'+date.slice(0,4);
 const folder=`https://onedrive.live.com/my?id=${encodeURIComponent('/personal/fixture/Documents/'+folderName)}`;
 const files={worship:'聖道教會'+date+'主日崇拜.pptx',sermon:'講道-'+date+'.pptx'};
 const ids={worship:'11111111-1111-1111-1111-111111111111',sermon:'22222222-2222-2222-2222-222222222222'};
 let openCount=0, slow=0, broken=false, login=false;
 const server=http.createServer((_req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({date,timezone:'America/Chicago',folderName,folderUrl:folder,folderExists:true}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));website.apiURL=`http://127.0.0.1:${server.address().port}/api/upcoming`;
 officeSession.protocol.handle('https',async request=>{
  const url=new URL(request.url);
  if(url.hostname==='onedrive.live.com'&&url.pathname==='/my'){
   if(login)return new Response('<!doctype html><script>location.href="https://login.live.com/test"</script>',{headers:{'Content-Type':'text/html; charset=utf-8'}});
   const buttons=Object.entries(files).map(([deck,name])=>`<button onclick="window.open('https://onedrive.live.com/personal/fixture/_layouts/15/Doc.aspx?sourcedoc={${ids[deck]}}&file=${encodeURIComponent(name)}&CT=${Date.now()}')">${name}</button>`).join('');
   return new Response('<!doctype html><title>Weekly folder</title>'+buttons,{headers:{'Content-Type':'text/html; charset=utf-8'}});
  }
  if(url.hostname==='login.live.com')return new Response('<!doctype html><title>Microsoft Sign In</title><h1>Sign in</h1>',{headers:{'Content-Type':'text/html; charset=utf-8'}});
  if(url.hostname==='onedrive.live.com'&&/Doc.aspx$/.test(url.pathname)){
   const deck=url.searchParams.get('file')===files.worship?'worship':'sermon';openCount++;
   return new Response(`<!doctype html><title>${files[deck]}</title><iframe allowfullscreen allow="fullscreen" src="https://powerpoint.officeapps.live.com/test?deck=${deck}" style="position:fixed;inset:0;width:100vw;height:100vh;border:0"></iframe>`,{headers:{'Content-Type':'text/html; charset=utf-8'}});
  }
  if(url.hostname==='powerpoint.officeapps.live.com'){
   const deck=url.searchParams.get('deck'),fail=broken&&deck==='sermon';
   return new Response(`<!doctype html><title>${files[deck]}</title><header>PowerPoint Private header</header><div id="SlidePanel" aria-busy="true" style="width:100vw;height:100vh;background:#152b44"></div><button id="status">SLIDE 5 OF 40</button><script>
    window.index=5;document.onkeydown=e=>{if(e.key==='Home')index=1;if(e.key==='ArrowRight')index++;document.querySelector('#status').textContent='SLIDE '+index+' OF 40';};
    setTimeout(()=>{${fail?'':`const c=document.createElement('canvas');c.width=800;c.height=450;c.style.width='100%';c.style.height='100%';c.getContext('2d').fillText('Actual rendered fixture slide',20,40);document.querySelector('#SlidePanel').append(c);document.querySelector('#SlidePanel').setAttribute('aria-busy','false');`}},${slow});
   </script>`,{headers:{'Content-Type':'text/html; charset=utf-8'}});
  }
  return new Response(null,{status:404});
 });
 const clear=async()=>{await preparer.running;for(const page of [...stage.pages.values()]){stage.remove(page.id);page.view.webContents.close();}};
 try{
  await clear();slow=500;
  await act('prepare-week');const first=preparer.running;await act('prepare-week');assert.equal(preparer.running,first);
  await first;assert.equal(preparer.state.status,'ready',preparer.state.message);assert.equal(openCount,2);assert.equal(stage.get(stage.active).deck,'worship');
  const before=[...stage.pages.values()].length,worship=stage.get(stage.selected.worship),sermon=stage.get(stage.selected.sermon);
  assert.ok(worship.ready&&sermon.ready);
  const office=page=>page.view.webContents.mainFrame.framesInSubtree.find(f=>{try{return new URL(f.url).hostname==='powerpoint.officeapps.live.com';}catch{return false;}});
  assert.equal(await office(worship).executeJavaScript('index'),1);assert.equal(await office(sermon).executeJavaScript('index'),1);
  await act('key',{key:'Right'});const index=await office(worship).executeJavaScript('index');assert.equal(index,2);
  await act('prepare-week');await preparer.running;assert.equal(openCount,2);assert.equal(stage.pages.size,before);assert.equal(await office(worship).executeJavaScript('index'),index);
  await act('take',{deck:'sermon'});await act('take',{deck:'worship'});
  // A single deck navigation revokes green status. Retry restores that page only.
  sermon.view.webContents.reload();await until(()=>!sermon.ready,'navigation invalidated');assert.equal(preparer.state.status,'blocked');
  await act('prepare-week');await preparer.running;assert.equal(preparer.state.status,'ready',preparer.state.message);assert.equal(await office(worship).executeJavaScript('index'),index);
  assert.equal(stage.pages.size,before);assert.equal(stage.active,worship.id);
  // Render failure never arms/presents a login/header/empty slide surface.
  await clear();broken=true;slow=0;preparer.driver.surfaceTimeoutMs=500;
  await act('prepare-week');await preparer.running;assert.equal(preparer.state.status,'blocked');assert.equal(stage.active,null);assert.equal(preparer.state.decks.sermon,'blocked');
  broken=false;preparer.driver.surfaceTimeoutMs=5000;
  const failed=[...stage.pages.values()].find(p=>p.deck==='sermon'&&p.documentKey);failed.error='retry';
  await act('prepare-week');await preparer.running;assert.equal(preparer.state.status,'ready',preparer.state.message);
  // Login waits visibly; a subsequent login/navigation resumes the same run.
  await clear();login=true;await act('prepare-week');await until(()=>preparer.state.status==='waiting-login','login state');
  const run=preparer.running,signIn=[...stage.pages.values()].find(p=>p.view.webContents.getURL().includes('login.live.com'));
  login=false;await signIn.view.webContents.loadURL(folder);await run;assert.equal(preparer.state.status,'ready',preparer.state.message);
  await until(async()=> (await controller.webContents.executeJavaScript("document.querySelector('#preparation-status').textContent")).includes('就緒'),'ready controller status');
  console.log('PREPARATION SMOKE PASS',JSON.stringify({coldTwoFiles:true,actualFullscreen:true,firstSlides:true,slowRenderWait:true,duplicatePressCoalesced:true,warmRepeatKeepsPage:true,noDuplicateWindows:true,navigationRetryOnlyFailedDeck:true,emptySurfaceBlocked:true,loginResumesSameRun:true,meetAutomation:false}));
 }finally{await preparer.running;server.close();officeSession.protocol.unhandle('https');}
};
