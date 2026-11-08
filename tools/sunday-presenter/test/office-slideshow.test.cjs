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
