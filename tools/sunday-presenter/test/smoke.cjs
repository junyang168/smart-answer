const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const fixture = `<!doctype html><html><head><title>Fixture.pptx</title></head>
<body style="margin:0;background:#103c50;color:white;height:100vh;font:48px sans-serif">
<p id="slide">Audience slide</p><button onclick="window.open('/show','show')">Present</button>
<button onclick="document.documentElement.requestFullscreen()">Fullscreen</button>
<script>
window.token = Math.random(); window.steps = 0; window.keyLog = [];
document.addEventListener('keydown', e => { keyLog.push(e.key); if (e.key === 'ArrowRight') steps++;
document.querySelector('#slide').textContent = 'Slide / animation step ' + steps; });
window.sensitiveBridge = typeof window.presenter;
</script></body></html>`;

async function until(check, message) {
  const end = Date.now() + 15000;
  while (Date.now() < end) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw Error(`Timed out: ${message}`);
}

exports.run = async ({ stage, act, output, preparation, controller, toggleControls, website, remoteControl, hostVideo, controlShortcut, setOrigin }) => {
  const { globalShortcut, screen } = require('electron');
  let postedBody, apiFailure = false, apiCalls = 0;
  const server = http.createServer((request, response) => {
    if (request.url === '/api/upcoming') {
      apiCalls++;
      if (apiFailure) { response.writeHead(503); response.end(); return; }
      const date = require('../website.cjs').comingSunday();
      const [year, month, day] = date.split('-').map(Number);
      const folderName = `${month}-${day}-${year}`;
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({ date, folderName, timezone: 'America/Chicago', folderExists: true,
        folderUrl: `https://onedrive.live.com/my?id=%2Fpersonal%2Ffixture%2FDocuments%2F${folderName}` }));
    } else if (request.url === '/embedded') {
      response.setHeader('Content-Type', 'text/html');
      response.end('<!doctype html><title>Embedded.pptx</title><iframe sandbox="allow-scripts" src="/frame" style="border:0;width:100vw;height:100vh"></iframe>');
    } else if (request.url === '/post') {
      postedBody = '';
      request.on('data', data => { postedBody += data; });
      request.on('end', () => { response.setHeader('Content-Type', 'text/html'); response.end(fixture); });
    } else if (request.url === '/redirect') { response.writeHead(302, { Location: '/private' }); response.end(); }
    else { response.setHeader('Content-Type', 'text/html'); response.end(fixture); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  setOrigin(origin);
  const savedOutputID = output.id;
  const audienceFullscreen = () => {
    if (process.platform !== 'darwin') return output.isFullScreen();
    const b = output.getBounds(), d = screen.getDisplayMatching(b).bounds;
    return b.x === d.x && b.y === d.y && b.width === d.width && b.height === d.height;
  };
  const evidence = path.join(__dirname, '..', 'output', 'smoke');
  fs.mkdirSync(evidence, { recursive: true });
  const wc = page => page.view.webContents;
  const load = async deck => {
    await act('open', { deck, url: `${origin}/${deck}` });
    const page = stage.get(stage.selected[deck]);
    await until(() => page.loaded && !wc(page).isLoading(), 'fixture loaded');
    assert.equal(controller.isVisible(), true);
    assert.equal(preparation.isVisible(), true);
    toggleControls();
    assert.equal(controller.isVisible(), true);
    assert.equal(preparation.isVisible(), true);
    await act('hide-panel');
    assert.equal(preparation.isVisible(), true);
    await act('arm', { id: page.id });
    return page;
  };
  try {
    assert.equal(controller.isVisible(), false);
    assert.equal(globalShortcut.isRegistered(controlShortcut), true);
    assert.equal(output.isVisible(), false);
    assert.equal(output.isFullScreen(), false);
    assert.equal(controller.isFullScreen(), false);
    assert.equal(controller.isFullScreenable(), false);
    toggleControls(); assert.equal(controller.isVisible(), true);
    require('electron').app.focus({ steal: true }); controller.focus();
    await until(() => controller.isFocused(), 'controller focused before hiding');
    toggleControls(); assert.equal(controller.isVisible(), true);
    controller.hide(); // With no slide, toggling never reveals a blank window.
    website.apiURL = origin + '/api/upcoming';
    await act('refresh-folder');
    assert.equal(apiCalls, 1);
    assert.equal(website.state.status, 'ready');
    await until(async () => (await controller.webContents.executeJavaScript("document.querySelector('#sunday-date').textContent")).includes(website.state.folder.date), 'website date shown');
    assert.equal(stage.pages.size, 0);
    assert.equal(controller.isVisible(), false);
    // Old persisted Meet settings must not restore any Phase 1 automation.
    const phase1 = await controller.webContents.executeJavaScript(`(async () => {
      const state = (await window.presenter.act('state')).state;
      const removed = ['prepare-service','begin-service','end-service','test-recording','save-meeting','cancel-auto-start','confirm-share'];
      const replies = await Promise.all(removed.map(action => window.presenter.act(action)));
      return { noWorkflow: !('workflow' in state) && !('schedule' in state) && !('meetingConfigured' in state),
        noControls: !document.querySelector('#meeting-settings, #workflow-status, #schedule-status'),
        rejected: replies.every(reply => reply.error === 'Unknown action'),
        remoteAvailable: !!document.querySelector('#remote-start') };
    })()`);
    assert.deepEqual(phase1, {noWorkflow:true, noControls:true, rejected:true, remoteAvailable:true});
    console.log('smoke: loading');
    const worship = await load('worship'), sermon = await load('sermon');
    const tokens = await Promise.all([worship, sermon].map(p => wc(p).executeJavaScript('token')));
    if (process.argv.includes('--focus-smoke')) {
      await act('open', { deck: 'sermon', url: origin + '/embedded' });
      const embedded = stage.get(stage.selected.sermon);
      await until(() => embedded.loaded && wc(embedded).mainFrame.frames.length === 1, 'embedded slideshow loaded');
      const frame = wc(embedded).mainFrame.frames[0];
      await until(async () => await frame.executeJavaScript('typeof steps') === 'number', 'iframe keys loaded');
      await act('arm', { id: embedded.id }); await act('take', { deck: 'sermon' });
      await frame.executeJavaScript('window.focus(); document.body.tabIndex=0; document.body.focus()');
      controller.show(); controller.focus();
      await act('key', { key: 'Right' });
      await until(async () => await frame.executeJavaScript('steps') === 1, 'embedded frame receives arrow after focus restore');
      stage.remove(embedded.id); wc(embedded).close(); stage.selected.sermon = sermon.id;
      console.log('IFRAME FOCUS PASS');
    }
    assert.equal(await wc(worship).executeJavaScript('sensitiveBridge'), 'undefined');
    assert.equal(wc(worship).getLastWebPreferences().nodeIntegration, false);
    assert.equal(wc(worship).getLastWebPreferences().sandbox, true);
    assert.equal(wc(worship).session, wc(sermon).session);
    await wc(worship).session.cookies.set({ url: origin, name: 'shared-session', value: 'yes' });
    assert.match(await wc(sermon).executeJavaScript('document.cookie'), /shared-session=yes/);
    for (let i = 0; i < 20; i++) {
      const page = i % 2 ? sermon : worship;
      await act('take', { deck: page.deck });
      await until(audienceFullscreen, 'fullscreen only after verified slide');
      if (process.platform === 'darwin') assert.equal(output.isFullScreen(), false);
      await act('key', { key: 'Right' });
      try {
        await until(async () => await wc(page).executeJavaScript('steps') === Math.floor(i / 2) + 1, 'key delivery');
      } catch (error) {
        console.log({ i, steps: await wc(page).executeJavaScript('steps'), keys: await wc(page).executeJavaScript('keyLog'),
          focused: wc(page).isFocused(), windowFocused: output.isFocused(), active: stage.active, page: page.id });
        throw error;
      }
      assert.equal(output.id, savedOutputID);
      for (const other of stage.pages.values()) assert.equal(wc(other).isAudioMuted(), other !== page);
    }
    // Reproduce the owner's two-press failure: visibility remains true after
    // the fullscreen output takes focus. One toggle must raise, not hide it.
    controller.show();
    output.focus(); wc(sermon).focus();
    await until(() => !controller.isFocused(), 'visible controller behind output');
    assert.equal(controller.isVisible(), true);
    toggleControls();
    await until(() => controller.isVisible() && controller.isFocused(), 'one toggle raises covered controls');
    toggleControls();
    assert.equal(controller.isVisible(), true); // Recovery never accidentally hides the panel.
    const liveBeforeClose = stage.active;
    controller.close();
    assert.equal(controller.isVisible(), false);
    assert.equal(stage.active, liveBeforeClose);
    assert.equal(output.isVisible(), true);
    require('electron').app.emit('activate', {}, true);
    await until(() => controller.isVisible(), 'Dock activation restores the panel');
    await act('hide-panel');
    console.log('smoke: switching passed');
    assert.deepEqual(await Promise.all([worship, sermon].map(p => wc(p).executeJavaScript('token'))), tokens);
    assert.deepEqual(await Promise.all([worship, sermon].map(p => wc(p).executeJavaScript('steps'))), [10, 10]);
    await remoteControl.start({ host: '127.0.0.1' });
    const controlURL = `http://127.0.0.1:${remoteControl.server.address().port}`;
    const pair = await fetch(controlURL + '/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: remoteControl.snapshot().code }) }).then(r => r.json());
    const remoteCommand = { key: 'Right', id: require('node:crypto').randomUUID(), binding: pair.binding };
    const flip = () => fetch(controlURL + '/key', { method: 'POST', headers: {
      'Content-Type': 'application/json', Authorization: 'Bearer ' + pair.token }, body: JSON.stringify(remoteCommand) });
    assert.equal((await flip()).status, 200); assert.equal((await flip()).status, 200);
    await until(async () => await wc(sermon).executeJavaScript('steps') === 11, 'one remote step despite retry');
    await act('key', { key: 'Right' });
    await until(async () => await wc(sermon).executeJavaScript('steps') === 12, 'operator still controls the same page');
    await remoteControl.stop();
    if (process.argv.includes('--recording-hardware-smoke')) {
      await require('./hardware-recording-smoke.cjs').run({ hostVideo, act, evidence });
    }
    if (process.argv.includes('--recording-smoke')) {
      await require('./recording-smoke.cjs').run({ hostVideo, stage, act, output, worship, sermon, evidence });
    }
    if (process.argv.includes('--video-smoke')) {
      await require('./video-smoke.cjs').run({ hostVideo, stage, act, output, worship, sermon, evidence });
    }
    const liveBeforeRefresh = stage.active;
    apiFailure = true;
    await assert.rejects(act('refresh-folder'), /503/);
    assert.equal(stage.active, liveBeforeRefresh);
    assert.equal(website.state.folder, null);
    apiFailure = false; await act('refresh-folder');
    assert.equal(stage.active, liveBeforeRefresh);
    toggleControls();
    await controller.webContents.executeJavaScript(`void (window.savedArm = document.querySelector('[data-page="${worship.id}"] [data-action="arm"]'))`);
    worship.title = 'Fixture title update.pptx'; stage.changed();
    await until(async () => await controller.webContents.executeJavaScript(`document.querySelector('[data-page="${worship.id}"] .title').textContent.includes('Fixture title update')`), 'control update');
    assert.equal(await controller.webContents.executeJavaScript(`savedArm === document.querySelector('[data-page="${worship.id}"] [data-action="arm"]')`), true);
    fs.writeFileSync(path.join(evidence, 'control.png'), (await controller.webContents.capturePage()).toPNG());
    await act('hide-panel');
    fs.writeFileSync(path.join(evidence, 'slide.png'), (await wc(sermon).capturePage()).toPNG());
    output.focus(); wc(sermon).focus();
    wc(sermon).sendInputEvent({ type: 'keyDown', keyCode: 'P', modifiers: [process.platform === 'darwin' ? 'meta' : 'control'] });
    wc(sermon).sendInputEvent({ type: 'keyUp', keyCode: 'P', modifiers: [process.platform === 'darwin' ? 'meta' : 'control'] });
    await until(() => controller.isVisible(), 'Command-P intercepted before the presentation page');
    toggleControls();
    // The main cards keep duplicate/internal pages collapsed and offer one
    // confirmation action instead of exposing arm/take as separate steps.
    assert.equal(await controller.webContents.executeJavaScript("document.querySelectorAll('#decks > .deck').length"), 2);
    assert.equal(await controller.webContents.executeJavaScript("[...document.querySelectorAll('.advanced')].every(el => !el.open)"), true);
    await act('prepare', { id: worship.id });
    toggleControls();
    await until(async () => await controller.webContents.executeJavaScript("document.querySelector('#worship .primary').textContent.includes('確認並播放')"), 'single confirmation button');
    await controller.webContents.executeJavaScript("document.querySelector('#worship .primary').click()", true);
    await until(() => stage.active === worship.id && !controller.isVisible(), 'playback automatically hides settings');
    assert.equal(controller.getBounds().width, 760);
    toggleControls();
    assert.equal(await controller.webContents.executeJavaScript("document.querySelectorAll('[data-key], #panel-settings').length"), 0);
    assert.equal(controller.isVisible(), true);
    // Taking a deck again also hides settings, even for the same deck.
    stage.take('worship');
    assert.equal(controller.isVisible(), false);
    assert.equal(controller.isFullScreen(), false);
    await act('hide-panel');
    // Deck changes must not reopen a panel the operator explicitly dismissed.
    for (const [key, deck] of [['2','sermon'],['1','worship']]) {
      const shown = stage.get(stage.active); output.focus(); wc(shown).focus();
      wc(shown).sendInputEvent({type:'keyDown',keyCode:key,modifiers:[process.platform==='darwin'?'meta':'control']});
      wc(shown).sendInputEvent({type:'keyUp',keyCode:key,modifiers:[process.platform==='darwin'?'meta':'control']});
      await until(() => stage.active === stage.selected[deck], 'formal presentation deck shortcut');
      assert.equal(controller.isVisible(), false);
    }
    await act('take', { deck: 'sermon' });
    // Real window.open path: returned WC retains an opener and is private/muted.
    await wc(sermon).executeJavaScript("void window.open('/show', 'show')");
    await until(() => stage.pages.size === 3 && [...stage.pages.values()].at(-1).loaded &&
      !wc([...stage.pages.values()].at(-1)).isLoading(), 'popup');
    const popup = [...stage.pages.values()].at(-1);
    console.log('smoke: popup loaded');
    assert.notEqual(popup.id, sermon.id);
    assert.equal(stage.active, sermon.id);
    assert.equal(stage.selected.sermon, sermon.id);
    assert.equal(stage.preview, null);
    assert.equal(popup.view.getVisible(), false);
    assert.equal(wc(popup).isAudioMuted(), true);
    assert.equal(await wc(popup).executeJavaScript('!!window.opener'), true);
    await wc(sermon).executeJavaScript(`(() => { const form = document.createElement('form'); form.method = 'POST';
      form.action = '/post'; form.target = 'post-popup'; const input = document.createElement('input');
      input.name = 'evidence'; input.value = 'preserved-post'; form.append(input); document.body.append(form); form.submit(); })()`, true);
    await until(() => stage.pages.size === 4 && [...stage.pages.values()].at(-1).loaded, 'POST popup');
    const postPage = [...stage.pages.values()].at(-1);
    assert.equal(postedBody, 'evidence=preserved-post');
    assert.equal(wc(postPage).isAudioMuted(), true);
    assert.equal(stage.selected.sermon, sermon.id);
    stage.remove(postPage.id); wc(postPage).close();
    await act('prepare', { id: popup.id });
    await act('arm', { id: popup.id }); await act('take', { deck: 'sermon' });
    console.log('smoke: fullscreen');
    // Exercise HTML fullscreen with user activation and preserve native output ID.
    output.focus(); wc(popup).focus();
    await wc(popup).executeJavaScript('void document.documentElement.requestFullscreen()', true);
    await until(async () => await wc(popup).executeJavaScript('!!document.fullscreenElement'), 'fullscreen entry');
    assert.equal(await wc(popup).executeJavaScript('!!document.fullscreenElement'), true);
    assert.equal(audienceFullscreen(), true);
    if (process.platform === 'darwin') assert.equal(output.isFullScreen(), false);
    assert.equal(output.id, savedOutputID);
    await wc(popup).executeJavaScript('document.exitFullscreen()');
    await until(() => !popup.ready, 'fullscreen exit disarms');
    assert.equal(stage.active, null);
    // Full-page redirect/navigation cannot leave an editor or login on air.
    stage.prepare(popup.id); stage.arm(popup.id); stage.take('sermon');
    await wc(popup).loadURL(`${origin}/redirect`);
    assert.equal(stage.active, null); assert.equal(popup.ready, false);
    assert.equal(wc(popup).isAudioMuted(), true);
    stage.layout();
    assert.equal(worship.view.getBounds().width, output.getContentSize()[0]);
    stage.remove(popup.id); wc(popup).close();
    assert.equal(stage.pages.size, 2);
    await act('take', { deck: 'worship' });
    wc(worship).forcefullyCrashRenderer();
    await until(() => !worship.ready, 'crash disarms');
    assert.equal(stage.active, null);
    const result = { platform: process.platform, versions: process.versions,
      switches: 20, steps: [10, 10], stableOutput: true, popupOpener: true,
      fullscreenContained: true, sharedCookies: true, inactiveMuted: true,
      navigationAndCrashBlackout: true, popupPostPreserved: true, fullscreenOutput: true,
      controlsHiddenByDefault: true, commandPRegistered: true, stableControlButtons: true,
      presentationCommandPIntercepted: true, coveredControlsRaisedWithOneToggle: true, noBlankStartupWindow: true, controllerCannotFullscreen: true, playbackHidesSettings: true, noFlipPanel: true, closePreservesPresentation: true, dockActivationRestoresPanel: true, formalDeckShortcuts: true,
      websiteFolderFetch: true, failedRefreshPreservesLivePresentation: true,
      remoteAndOperatorFlipSamePage: true, remoteDuplicateIdAppliedOnce: true,
      phase1ManualMeet: true, removedMeetActionsRejected: true, microsoftAuthentication: 'NOT TESTED' };
    fs.writeFileSync(path.join(evidence, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } finally { server.close(); }
};
