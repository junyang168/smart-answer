const { app, BaseWindow, BrowserWindow, WebContentsView, ipcMain, session, Menu, globalShortcut, dialog, powerSaveBlocker } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { Stage, allowedURL } = require('./stage.cjs');
const { identifyFiles, fileElements, isDocumentURL, sameFolderURL } = require('./folder-wizard.js');
const { Website } = require('./website.cjs');
const { protectSlideshowEnd } = require('./slideshow-end.js');
const { RemoteControl } = require('./remote.cjs');
const { identity } = require('./remote-identity.cjs');
const { HostVideo } = require('./host-video.cjs');
const { installOfficeView, viewDocumentURL, isOfficeDocument } = require('./office-view.cjs');
const { enterOfficeSlideShow, officeSlideShowActive, runOfficeAction } = require('./office-slideshow.cjs');

const smoke = process.argv.includes('--smoke');
if (smoke) app.setPath('userData', require('node:fs').mkdtempSync(path.join(app.getPath('temp'), 'sunday-presenter-')));
else app.setPath('userData', path.join(app.getPath('appData'), 'Sunday Presenter'));
app.setName('Sunday Presenter');
if (!app.requestSingleInstanceLock()) app.quit();
let controller, output, preparation, stage, officeSession, quitting = false, quitReady = false, quitCleanupDone = false, quitTask, testOrigin;
let website, remoteControl, awakeBlocker, hostVideo;
let keyQueue = Promise.resolve();
let folderTimer, choosingRecording = false;
let filePlan = null;
let folderMessage = '';
const folderRuns = new Map();
const controlURL = pathToFileURL(path.join(__dirname, 'control.html')).href;
const labels = { worship: '主日崇拜', sermon: '講道' };
const controlShortcut = smoke ? 'CommandOrControl+Shift+Alt+P' : 'CommandOrControl+P';

function toggleControls() {
  // Recovery action is idempotent. Close/hide is an explicit panel action.
  if (!controller || controller.isDestroyed()) return;
  if (controller.isMinimized()) controller.restore();
  controller.show(); controller.moveTop(); controller.focus();
}

function changed(reason) {
  if (quitCleanupDone) return;
  // Every successful presentation, leaves only slides.
  if (reason === 'present' && controller && !controller.isDestroyed()) {
    controller.hide();
    output.focus();
    stage.get(stage.active).view.webContents.focus();
  }
  if (stage) remoteControl?.observe(stage.active);
  hostVideo?.observe();
  if (stage && controller && !controller.isDestroyed()) controller.webContents.send('presenter:state', snapshot());
}

function snapshot() { return { ...stage.snapshot(), website: website?.state, remote: remoteControl?.snapshot(),
  folderMessage, recording: hostVideo?.recording.snapshot() }; }


function remotePreferences() {
  return { session: officeSession, nodeIntegration: false, nodeIntegrationInSubFrames: false,
    contextIsolation: true, sandbox: true, webSecurity: true, backgroundThrottling: false,
    disableHtmlFullscreenWindowResize: true, safeDialogs: true,
    navigateOnDragDrop: false, autoplayPolicy: 'document-user-activation-required' };
}

function createPage(deck, popupOptions = {}, followFrom = null) {
  if ([...stage.pages.values()].filter(p => p.deck === deck).length >= 8) {
    throw Error('此簡報已開啟八個頁面；請先關閉不需要的頁面。');
  }
  // The popup creation callback preserves window.opener and Office's POST body.
  // Never replace a popup by loading its URL in the editor.
  const options = { webPreferences: { ...popupOptions.webPreferences, ...remotePreferences() } };
  // Electron has already created the popup WebContents. Adopt that exact object,
  // otherwise the popup navigation and opener belong to a different, orphaned WC.
  if (popupOptions.webContents) options.webContents = popupOptions.webContents;
  const view = new WebContentsView(options);
  const popup = !!popupOptions.webContents;
  const page = stage.add(deck, view, { select: !popup });
  const wc = view.webContents;
  wc.setWindowOpenHandler(({ url, postBody }) => {
    // Repeated GET clicks reuse the existing private document; never replace a
    // POST popup or destroy its opener/body. Authentication popups stay parked.
    if (!postBody && isDocumentURL(url)) {
      const existing = [...stage.pages.values()].find(p => p.view.webContents.getURL() === viewDocumentURL(url));
      if (existing) {
        if (stage.preview === page.id && !existing.ready) {
          stage.prepare(existing.id); controller.moveTop();
        } else if (existing.ready) { stage.selected[existing.deck] = existing.id; changed(); }
        return { action: 'deny' };
      }
    }
    if (!allowedURL(url, testOrigin) || [...stage.pages.values()].filter(p => p.deck === deck).length >= 8) {
      return { action: 'deny' };
    }
    return { action: 'allow', overrideBrowserWindowOptions: { webPreferences: remotePreferences() },
      createWindow: options => createPage(deck, options, { id: page.id, url }).view.webContents };
  });
  const navigate = event => {
    if (!allowedURL(event.url, testOrigin)) event.preventDefault();
    if (event.isMainFrame !== false) {
      page.loaded = false;
      stage.invalidate(page.id);
    }
  };
  wc.on('will-frame-navigate', navigate);
  wc.on('will-redirect', navigate);
  wc.on('did-start-navigation', (_event, _url, inPlace, mainFrame) => {
    if (mainFrame && !inPlace) { page.loaded = false; stage.invalidate(page.id); }
  });
  wc.on('did-finish-load', () => {
    page.loaded = true; page.error = '';
    const name = (() => { try { return new URL(wc.getURL()).searchParams.get('file') || ''; } catch { return ''; } })();
    const assigned = filePlan && Object.keys(filePlan).find(key => name === filePlan[key]);
    if (assigned && assigned !== page.deck) {
      if (stage.selected[page.deck] === page.id) stage.selected[page.deck] = null;
      page.deck = assigned;
    }
    if (followFrom && isDocumentURL(wc.getURL()) && stage.preview === followFrom.id) {
      stage.prepare(page.id); controller.moveTop();
    }
    changed();
  });
  // Office renders the show inside a cross-origin iframe. Protect its own
  // document as well as the outer page, without replacing/reloading either.
  wc.on('did-frame-finish-load', (_event, _mainFrame, processId, routingId) => {
    try {
      const frame = require('electron').webFrameMain.fromId(processId, routingId);
      frame?.executeJavaScript(`(${protectSlideshowEnd.toString()})()`).catch(() => {});
    } catch { /* A frame may detach while its load event is being delivered. */ }
  });
  wc.on('did-stop-loading', changed);
  wc.on('page-title-updated', (_event, title) => { page.title = title.slice(0, 180); changed(); });
  wc.on('did-fail-load', (_event, code, _description, _url, mainFrame) => {
    if (mainFrame && code !== -3) stage.invalidate(page.id, `載入失敗 (${code})；請在準備視窗重試。`);
  });
  wc.on('render-process-gone', () => stage.invalidate(page.id, '頁面已停止。請關閉後重新載入。'));
  wc.on('leave-html-full-screen', () => stage.invalidate(page.id));
  wc.on('before-input-event', (event, input) => {
    // Catch Command-P before PowerPoint, including app-directed key events
    // that do not pass through the OS global shortcut.
    if (input.type === 'keyDown' && input.key.toLowerCase() === 'p' &&
        (process.platform === 'darwin' ? input.meta : input.control) && !input.alt && !input.shift) {
      event.preventDefault(); toggleControls(); return;
    }
    if (input.type === 'keyDown' && (process.platform === 'darwin' ? input.meta : input.control) &&
        !input.alt && !input.shift && ['1', '2'].includes(input.key)) {
      event.preventDefault();
      act('take', { deck: input.key === '1' ? 'worship' : 'sermon' }).catch(() => {});
      return;
    }
    if (stage.active === page.id && input.type === 'keyDown' && input.key === 'Escape') stage.invalidate(page.id);
  });
  wc.on('destroyed', () => stage.remove(page.id));
  // Office can open unrelated background authentication popups repeatedly.
  // Keep popups hidden/muted without stealing preview, focus, or deck selection.
  // The operator opens the appropriate new row with Prepare when needed.
  if (!popup) { stage.prepare(page.id); controller.moveTop(); }
  return page;
}

async function verifyAudiencePage(page) {
  const wc = page.view.webContents;
  if (!isOfficeDocument(wc.getURL())) return;
  const states = await Promise.all(wc.mainFrame.framesInSubtree.map(async frame => {
    try {
      if (new URL(frame.url).hostname !== 'powerpoint.officeapps.live.com') return false;
      return await runOfficeAction(frame, `(${officeSlideShowActive.toString()})(document)`);
    } catch { return false; }
  }));
  if (!states.some(Boolean)) throw Error('請先按「開始投影片放映」；含有 PowerPoint 標誌與帳號的準備頁不能播給觀眾。');
}

async function act(action, args = {}) {
  switch (action) {
    case 'state': return snapshot();
    case 'hide-panel': controller.hide(); if (stage.active !== null) { output.focus(); stage.get(stage.active).view.webContents.focus(); } break;
    case 'choose-week': {
      filePlan = null; folderMessage = '登入後會自動打開本週敬拜和講道 PPT。';
      const folder = await website.refresh();
      const existing = [...stage.pages.values()].find(p => sameFolderURL(p.view.webContents.getURL(), folder.folderUrl));
      if (existing) { stage.prepare(existing.id); controller.moveTop(); }
      else await act('open', { deck: 'worship', url: folder.folderUrl });
      folderRuns.set(stage.preview, { url: folder.folderUrl, opened: new Set(), busy: false, deadline: Date.now() + 120000 });
      break;
    }
    case 'record-devices': await hostVideo.recording.devices(); break;
    case 'record-start': {
      if (choosingRecording || hostVideo.recording.run) throw Error('錄影操作尚未完成。');
      choosingRecording = true;
      try {
        if (!hostVideo.ready()) throw Error('請先播放已確認的 PPT，再開始錄影。');
        const result = await dialog.showSaveDialog(controller, { title: '儲存 PPT＋麥克風錄影',
          defaultPath: path.join(app.getPath('videos'), `Sunday-${new Date().toISOString().replace(/[:.]/g, '-')}.mp4`),
          filters: [{ name: 'MP4 影片', extensions: ['mp4'] }] });
        if (result.canceled || !result.filePath) break;
        await hostVideo.recording.start({ target: result.filePath, deviceId: args.deviceId });
      } finally { choosingRecording = false; }
      break;
    }
    case 'record-stop': await hostVideo.recording.stop(); break;
    case 'record-reveal': hostVideo.recording.reveal(); break;
    case 'remote-forget': await remoteControl.forget(); break;
    case 'remote-pair': remoteControl.openPairing(); break;
    case 'remote-start': await remoteControl.start(); break;
    case 'remote-stop': await remoteControl.stop(); break;
    case 'refresh-folder': await website.refresh(); break;
    case 'sunday-folder': {
      if (!Object.hasOwn(labels, args.deck)) throw Error('Unknown deck');
      // Fetch again on each open: never silently reuse last Sunday's link.
      const folder = await website.refresh();
      return act('open', { deck: args.deck, url: folder.folderUrl });
    }
    case 'office': return act('open', { deck: args.deck, url: 'https://www.microsoft365.com/launch/powerpoint' });
    case 'open': {
      if (!Object.hasOwn(labels, args.deck) || typeof args.url !== 'string' ||
          args.url === 'about:blank' || !allowedURL(args.url, testOrigin)) throw Error('請貼上 HTTPS 的 Microsoft 365 / OneDrive 簡報連結。');
      const page = createPage(args.deck);
      // Failure details are surfaced by did-fail-load; never log private URLs.
      page.view.webContents.loadURL(viewDocumentURL(args.url)).catch(() => {});
      break;
    }
    case 'prepare': stage.prepare(args.id); controller.moveTop(); break;
    case 'start-slideshow': {
      const page = stage.get(args.id), wc = page.view.webContents;
      if (!isOfficeDocument(wc.getURL())) throw Error('請先開啟 PowerPoint 唯讀準備頁。');
      controller.hide(); stage.prepare(page.id);
      try {
        let entered = false;
        for (const frame of wc.mainFrame.framesInSubtree) {
          let officeFrame = false;
          try { officeFrame = new URL(frame.url).hostname === 'powerpoint.officeapps.live.com'; } catch {}
          if (!officeFrame) continue;
          entered = await runOfficeAction(frame, `(${enterOfficeSlideShow.toString()})(document)`, true);
          if (entered) break;
        }
        if (!entered) throw Error('PowerPoint 投影片尚未完成載入，請稍後再按一次。');
        page.officeFullscreen = true;
      } finally { toggleControls(); }
      break;
    }
    case 'confirm-play': {
      const page = stage.get(args.id);
      await verifyAudiencePage(page);
      stage.arm(page.id); stage.take(page.deck); break;
    }
    case 'arm': {
      const page = stage.get(args.id);
      await verifyAudiencePage(page);
      stage.arm(page.id); break;
    }
    case 'take':
      if (!Object.hasOwn(labels, args.deck)) throw Error('Unknown deck');
      await verifyAudiencePage(stage.get(stage.selected[args.deck]));
      stage.take(args.deck); break;
    case 'close': {
      const page = stage.get(args.id);
      stage.remove(page.id);
      page.view.webContents.close();
      break;
    }
    case 'key': {
      if (!['Right', 'Left'].includes(args.key)) throw Error('Unknown key');
      const active = stage.active;
      const task = keyQueue.then(() => sendSlideKey(args.key, active, args.valid));
      keyQueue = task.catch(() => {});
      await task;
      break;
    }
    case 'show-output': if (stage.active !== null) { controller.hide(); output.show(); output.focus(); stage.get(stage.active).view.webContents.focus(); } break;
    default: throw Error('Unknown action');
  }
  return snapshot();
}

async function sendSlideKey(key, expectedActive, valid = () => true) {
      if (stage.active !== expectedActive || !valid()) throw Error('畫面已切換，請再按一次。');
      if (stage.active === null) throw Error('尚未播放簡報。');
      const wc = stage.get(stage.active).view.webContents;
      const focusedFrame = wc.focusedFrame;
      app.focus({ steal: true });
      output.show();
      output.focus();
      const focusDeadline = Date.now() + 3000;
      while (!output.isFocused() && Date.now() < focusDeadline) {
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      wc.focus();
      while (!wc.isFocused() && Date.now() < focusDeadline) {
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      if (!output.isFocused() || !wc.isFocused()) {
        if (smoke) console.log('focus failure', { window: output.isFocused(), page: wc.isFocused(), id: wc.id });
        throw Error('請先選取輸出視窗，再試一次。');
      }
      if (stage.active !== expectedActive || !valid()) throw Error('畫面已切換，請再按一次。');
      const frames = wc.mainFrame.framesInSubtree;
      const powerpoint = frames.filter(frame => {
        try { return /(^|\.)powerpoint\.officeapps\.live\.com$/.test(new URL(frame.url).hostname); } catch { return false; }
      });
      let target = focusedFrame && focusedFrame !== wc.mainFrame && frames.includes(focusedFrame) ? focusedFrame :
        powerpoint.length === 1 ? powerpoint[0] : wc.mainFrame;
      // Restore the actual slideshow document. A wrapper's focused WebContents
      // alone is insufficient for cross-origin/opaque embedded presentations.
      for (let depth = 0; depth < 5; depth++) {
        await Promise.race([
          target.executeJavaScript(`(() => {
            window.focus();
            const visible = [...document.querySelectorAll('iframe,frame')].filter(el => {
              const r = el.getBoundingClientRect(), style = getComputedStyle(el);
              return style.visibility !== 'hidden' && style.display !== 'none' &&
                r.width >= innerWidth * .8 && r.height >= innerHeight * .8 && r.right > 0 && r.bottom > 0;
            });
            const active = document.activeElement;
            const player = visible.includes(active) ? active : visible.length === 1 ? visible[0] : null;
            if (player) { player.focus({ preventScroll: true }); player.contentWindow?.focus(); }
          })()`, true),
          new Promise((_, reject) => setTimeout(() => reject(Error('投影片尚未取得焦點，請重新選取播放畫面。')), 1000))
        ]);
        if (stage.active !== expectedActive || !valid()) throw Error('畫面已切換，請再按一次。');
        const next = wc.focusedFrame;
        if (!next || next === target || !frames.includes(next)) break;
        target = next;
      }
      if (target !== wc.mainFrame) {
        if (wc.debugger.isAttached()) throw Error('請先關閉此簡報的開發工具，再試一次。');
        wc.debugger.attach('1.3');
        try {
          const code = key === 'Right' ? 'ArrowRight' : 'ArrowLeft';
          const vk = key === 'Right' ? 39 : 37;
          await wc.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'keyDown', key: code, code, windowsVirtualKeyCode: vk });
          await wc.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', key: code, code, windowsVirtualKeyCode: vk });
        } finally { if (wc.debugger.isAttached()) wc.debugger.detach(); }
      } else {
        wc.sendInputEvent({ type: 'keyDown', keyCode: key });
        wc.sendInputEvent({ type: 'keyUp', keyCode: key });
      }
}

app.whenReady().then(async () => {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'Sunday Presenter', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }] },
    { role: 'editMenu' },
    { label: 'Presentation', submenu: [
      { label: '顯示設定', accelerator: controlShortcut, click: toggleControls },
      { label: '主日崇拜', accelerator: 'CmdOrCtrl+1', click: () => act('take', { deck: 'worship' }).catch(() => {}) },
      { label: '講道', accelerator: 'CmdOrCtrl+2', click: () => act('take', { deck: 'sermon' }).catch(() => {}) }
    ] }, { role: 'windowMenu' }
  ]));
  officeSession = session.fromPartition(smoke ? 'smoke-office' : 'persist:office');
  installOfficeView(officeSession);
  const permissionAllowed = (wc, permission) => permission === 'fullscreen' && wc &&
    (stage?.preview === wc.id || stage?.active === wc.id);
  officeSession.setPermissionRequestHandler((wc, permission, callback) => callback(!!permissionAllowed(wc, permission)));
  officeSession.setPermissionCheckHandler((wc, permission) => !!permissionAllowed(wc, permission));
  officeSession.on('will-download', event => event.preventDefault());
  const hostOptions = { width: 1100, height: 660, backgroundColor: '#000000',
    fullscreenable: false, autoHideMenuBar: true };
  output = new BrowserWindow({ ...hostOptions, frame: false, show: false, enableLargerThanScreen: true, hasShadow: false, fullscreenable: process.platform !== 'darwin', title: 'Sunday Presenter — OUTPUT',
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  await output.loadFile(path.join(__dirname, 'output.html'));
  // Allocate the stable capture surface without showing a blank startup window.
  // Only a verified Stage.take reveals it; subsequent deck switches keep its ID.
  const display = require('electron').screen.getPrimaryDisplay();
  output.setBounds(display.bounds);
  output.on('show', () => {
    if (stage && stage.active !== null) {
      // A normal borderless window remains enumerable from Chrome's desktop.
      // macOS native fullscreen moves the surface to a separate Space.
      if (process.platform === 'darwin') output.setBounds(require('electron').screen.getDisplayMatching(output.getBounds()).bounds);
      else if (!output.isFullScreen()) output.setFullScreen(true);
    }
  });
  preparation = new BaseWindow({ ...hostOptions, frame: false, enableLargerThanScreen: true, hasShadow: false,
    title: 'Sunday Presenter — PRIVATE PREPARATION', show: false });
  controller = new BrowserWindow({ width: 760, height: 650, show: false, alwaysOnTop: true,
    type: process.platform === 'darwin' ? 'panel' : 'normal',
    fullscreenable: false, maximizable: false,
    title: 'Sunday Presenter — 控制台',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true,
      contextIsolation: true, nodeIntegration: false } });
  if (process.platform === 'darwin') {
    controller.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    preparation.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }
  preparation.setAlwaysOnTop(true);
  controller.setAlwaysOnTop(true, 'modal-panel');
  if (process.platform === 'darwin') {
    app.setActivationPolicy('regular');
    app.dock.show();
  }
  const panelArea = require('electron').screen.getPrimaryDisplay().workArea;
  controller.setPosition(panelArea.x + Math.max(0, panelArea.width - 780), panelArea.y + 30);
  stage = new Stage(output, preparation, changed);
  website = new Website({ apiURL: process.env.SUNDAY_PRESENTER_API_URL, changed });
  hostVideo = new HostVideo({ output, changed, ready: () => !!stage.pages.get(stage.active)?.ready && !stage.pages.get(stage.active).view.webContents.isDestroyed() && output.isVisible() && !output.isMinimized() });
  output.on('minimize', () => hostVideo.observe());
  output.on('hide', () => hostVideo.observe());
  let remoteIdentity, identityError;
  if (!smoke) {
    try { remoteIdentity = identity(path.join(app.getPath('userData'), 'remote')); }
    catch { identityError = '講員配對設定無法讀取，請保留設定檔並重試。'; }
  }
  remoteControl = new RemoteControl({ state: () => stage.snapshot(), changed,
    advertise: smoke ? () => () => {} : undefined,
    identity: remoteIdentity, video: hostVideo,
    key: (key, active, valid) => {
      const task = keyQueue.then(() => sendSlideKey(key, active, valid));
      keyQueue = task.catch(() => {}); return task;
    } });
  if (identityError) remoteControl.error = identityError;
  stage.layout();
  preparation.setPosition(...output.getPosition());
  if (!globalShortcut.register(controlShortcut, toggleControls)) {
    dialog.showErrorBox('無法啟用控制台快捷鍵', '⌘P 已被其他程式使用。請關閉衝突程式後重開 Sunday Presenter。');
    app.quit(); return;
  }
  output.on('resize', () => stage.layout());
  output.on('move', () => preparation.setPosition(...output.getPosition()));
  output.on('close', event => { if (!quitReady) event.preventDefault(); });
  preparation.on('close', event => {
    if (!quitReady) { event.preventDefault(); preparation.hide(); }
  });
  controller.on('close', event => {
    if (!quitReady) { event.preventDefault(); controller.hide(); }
  });
  controller.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  controller.webContents.on('will-navigate', event => event.preventDefault());
  ipcMain.handle('presenter:action', async (event, action, args) => {
    if (event.sender !== controller.webContents || event.senderFrame !== controller.webContents.mainFrame ||
        event.senderFrame.url !== controlURL) throw Error('Untrusted caller');
    try { return { state: await act(action, args) }; }
    catch (error) { return { error: error?.message || String(error) || '操作未完成。' }; }
  });
  if (smoke) controller.webContents.on('console-message', (_event, ...args) => console.log('controller:', ...args));
  await controller.loadFile(path.join(__dirname, 'control.html'));
  if (!smoke) {
    awakeBlocker = powerSaveBlocker.start('prevent-display-sleep');
  }
  if (!smoke) { controller.show(); controller.focus(); website.refresh().catch(() => {}); }
  folderTimer = setInterval(async () => {
    for (const [id, run] of folderRuns) {
      const page = stage.pages.get(id);
      if (!page || page.view.webContents.isDestroyed()) { folderRuns.delete(id); continue; }
      const wc = page.view.webContents;
      if (Date.now() > run.deadline) { folderRuns.delete(id); folderMessage = '自動選檔已停止；可重新按「開啟本週 PPT」，或在 OneDrive 選擇檔案。'; changed(); continue; }
      if (run.busy || !page.loaded || !sameFolderURL(wc.getURL(), run.url) || run.opened.size === 2) continue;
      run.busy = true;
      try {
        const names = await wc.executeJavaScript(`(${fileElements.toString()})().map(item => item.name)`);
        const plan = identifyFiles(names);
        if (!plan) { if (names.length) { folderMessage = '找到多份候選或缺少檔案，請在 OneDrive 選擇本週要用的 PPT。'; changed(); } continue; }
        filePlan = plan; folderMessage = '已找到本週敬拜與講道，檔名顯示在下方。'; changed();
        for (const name of Object.values(plan)) {
          if (run.opened.has(name)) continue;
          // Only click the exact file name observed above, in the same dated folder.
          if (!sameFolderURL(wc.getURL(), run.url)) break;
          const point = await wc.executeJavaScript(`(() => { const item = (${fileElements.toString()})().find(item => item.name === ${JSON.stringify(name)}); if (!item) return null; const r = item.element.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
          if (point) {
            wc.sendInputEvent({ type: 'mouseDown', ...point, button: 'left', clickCount: 1 });
            wc.sendInputEvent({ type: 'mouseUp', ...point, button: 'left', clickCount: 1 });
            run.opened.add(name);
          }
        }
        if (run.opened.size === 2) folderRuns.delete(id);
      } catch { /* Login/navigation is allowed to finish before the next bounded poll. */ }
      finally { run.busy = false; }
    }
  }, 1500);
  if (smoke) {
    try {
      await require('./test/smoke.cjs').run({ stage, act, output, preparation, controller, toggleControls,
        website, remoteControl, hostVideo, controlShortcut, setOrigin: origin => { testOrigin = origin; } });
      console.log('SMOKE PASS');
      app.quit();
    } catch (error) { console.error(error); await hostVideo?.close(); app.exit(1); }
  }
}).catch(async error => {
  if (smoke) console.error(error);
  else dialog.showErrorBox('Sunday Presenter 未能啟動', error.message);
  await hostVideo?.close(); app.exit(1);
});
app.on('activate', () => toggleControls());
app.on('second-instance', () => {
  if (controller && !controller.isDestroyed()) {
    app.focus({ steal: true });
    if (controller.isMinimized()) controller.restore();
    controller.show(); controller.moveTop(); controller.focus();
  }
});
app.on('before-quit', event => {
  if (!quitReady && hostVideo) {
    event.preventDefault(); quitting = true;
    quitTask ||= Promise.allSettled([hostVideo.close(), remoteControl?.stop()]).then(() => { quitReady = true; app.quit(); });
    return;
  }
  if (quitCleanupDone) return;
  quitCleanupDone = true; quitting = true;
  globalShortcut.unregisterAll();
  clearInterval(folderTimer);
  if (awakeBlocker !== undefined && powerSaveBlocker.isStarted(awakeBlocker)) powerSaveBlocker.stop(awakeBlocker);
  remoteControl?.stop();
  if (stage) {
    stage.black();
    // BaseWindow does not own/destroy the WebContents of child views.
    for (const page of [...stage.pages.values()]) {
      stage.remove(page.id);
      if (!page.view.webContents.isDestroyed()) page.view.webContents.close({ waitForBeforeUnload: false });
    }
  }
});
module.exports = { act };
