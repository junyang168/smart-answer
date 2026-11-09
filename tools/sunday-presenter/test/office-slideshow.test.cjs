const { test } = require('node:test');
const assert = require('node:assert/strict');
const { enterOfficeSlideShow, officeSlideShowActive, runOfficeAction } = require('../office-slideshow.cjs');

test('only the live slide surface enters fullscreen, leaving account and Office chrome outside', async () => {
  const document={fullscreenElement:null};
  const panel={requestFullscreen:async()=>{document.fullscreenElement=panel;}};
  document.getElementById=id=>id==='SlidePanel'?panel:null;
  assert.equal(officeSlideShowActive(document),false);
  assert.equal(await enterOfficeSlideShow(document),true);
  assert.equal(officeSlideShowActive(document),true);
  document.fullscreenElement={id:'Header'};
  assert.equal(officeSlideShowActive(document),false);
  document.fullscreenElement=null;
  assert.equal(officeSlideShowActive(document),false);
});

test('missing or refused fullscreen is never accepted as a clean audience page', async () => {
  assert.equal(await enterOfficeSlideShow({getElementById:()=>null}),false);
  const refused={getElementById:()=>({requestFullscreen:async()=>{throw Error('NotAllowedError');}})};
  await assert.rejects(enterOfficeSlideShow(refused),/NotAllowedError/);
  const ignored={getElementById:()=>({requestFullscreen:async()=>{}}),fullscreenElement:null};
  assert.equal(await enterOfficeSlideShow(ignored),false);
});


test('a stalled Office renderer times out instead of leaving the first-click action pending forever', async () => {
  await assert.rejects(runOfficeAction({executeJavaScript:()=>new Promise(()=>{})},'test',false,10),/逾時/);
  assert.equal(await runOfficeAction({executeJavaScript:async()=>true},'test',true,10),true);
});

test('preparation requires a laid-out rendered slide, not an empty panel or pending image', () => {
  const {officeSurfaceState}=require('../office-slideshow.cjs');
  let busy=null,images=[],canvases=[];
  const panel={getBoundingClientRect:()=>({width:100,height:100}),getAttribute:()=>busy,
    querySelectorAll:selector=>selector==='img'?images:selector==='canvas'?canvases:[]};
  const doc={getElementById:()=>panel,fullscreenElement:null,querySelectorAll:()=>[{textContent:'SLIDE 1 OF 45'}]};
  assert.equal(officeSurfaceState(doc).ready,false);
  canvases=[{width:100,height:100}];assert.equal(officeSurfaceState(doc).ready,true);
  images=[{src:'slide.png',getBoundingClientRect:()=>({width:100}),complete:false,naturalWidth:0}];assert.equal(officeSurfaceState(doc).ready,false);
  images=[];busy='true';assert.equal(officeSurfaceState(doc).ready,false);
  busy=null;doc.fullscreenElement=panel;assert.equal(officeSurfaceState(doc).fullscreen,true);assert.equal(officeSurfaceState(doc).slideIndex,1);
});


test('hidden empty Office accessibility image does not block a rendered slide', () => {
  const empty={getAttribute:()=>null,getBoundingClientRect:()=>({width:1}),complete:true,naturalWidth:0};
  const panel={getBoundingClientRect:()=>({width:100,height:100}),getAttribute:()=>null,
    querySelectorAll:selector=>selector==='img'?[empty]:selector==='canvas'?[{width:100,height:100}]:[]};
  const doc={getElementById:()=>panel,querySelectorAll:()=>[],defaultView:{getComputedStyle:()=>({visibility:'hidden'})}};
  assert.equal(require('../office-slideshow.cjs').officeSurfaceState(doc).ready,true);
});
