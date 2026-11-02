const { app, BaseWindow, BrowserWindow, WebContentsView, ipcMain, session, Menu, globalShortcut, dialog } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { Stage, allowedURL } = require('./stage.cjs');

const smoke = process.argv.includes('--smoke');
if (smoke) app.setPath('userData', require('node:fs').mkdtempSync(path.join(app.getPath('temp'), 'sunday-presenter-')));
else app.setPath('userData', path.join(app.getPath('appData'), 'Sunday Presenter'));
app.setName('Sunday Presenter');
if (!app.requestSingleInstanceLock()) app.quit();
let controller, output, preparation, stage, officeSession, quitting = false, testOrigin;
const controlURL = pathToFileURL(path.join(__dirname, 'control.html')).href;
const labels = { worship: '主日崇拜', sermon: '講道' };
const controlShortcut = 'CommandOrControl+P';

function toggleControls() {
  if (controller.isVisible()) {
    controller.hide(); preparation.hide();
    output.show(); output.focus();
    if (stage.active !== null) stage.get(stage.active).view.webContents.focus();
  } else {
    app.focus({ steal: true }); controller.show(); controller.focus();
  }
}

function changed() {
  if (controller && !controller.isDestroyed()) controller.webContents.send('presenter:state', stage.snapshot());
}

function remotePreferences() {
  return { session: officeSession, nodeIntegration: false, nodeIntegrationInSubFrames: false,
    contextIsolation: true, sandbox: true, webSecurity: true, backgroundThrottling: false,
    disableHtmlFullscreenWindowResize: true, safeDialogs: true,
    navigateOnDragDrop: false, autoplayPolicy: 'document-user-activation-required' };
}

function createPage(deck, popupOptions = {}) {
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
  wc.setWindowOpenHandler(({ url }) => {
    if (!allowedURL(url, testOrigin) || [...stage.pages.values()].filter(p => p.deck === deck).length >= 8) {
      return { action: 'deny' };
    }
    return { action: 'allow', overrideBrowserWindowOptions: { webPreferences: remotePreferences() },
      createWindow: options => createPage(deck, options).view.webContents };
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
  wc.on('did-finish-load', () => { page.loaded = true; page.error = ''; changed(); });
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
    if (stage.active === page.id && input.type === 'keyDown' && input.key === 'Escape') stage.invalidate(page.id);
  });
  wc.on('destroyed', () => stage.remove(page.id));
  // Office can open unrelated background authentication popups repeatedly.
  // Keep popups hidden/muted without stealing preview, focus, or deck selection.
  // The operator opens the appropriate new row with Prepare when needed.
  if (!popup) { stage.prepare(page.id); controller.hide(); }
  return page;
}

async function act(action, args = {}) {
  switch (action) {
    case 'state': return stage.snapshot();
    case 'office': return act('open', { deck: args.deck, url: 'https://www.microsoft365.com/launch/powerpoint' });
    case 'open': {
      if (!Object.hasOwn(labels, args.deck) || typeof args.url !== 'string' ||
          args.url === 'about:blank' || !allowedURL(args.url, testOrigin)) throw Error('請貼上 HTTPS 的 Microsoft 365 / OneDrive 簡報連結。');
      const page = createPage(args.deck);
      // Failure details are surfaced by did-fail-load; never log private URLs.
      page.view.webContents.loadURL(args.url).catch(() => {});
      break;
    }
    case 'prepare': stage.prepare(args.id); controller.hide(); break;
    case 'arm': stage.arm(args.id); break;
    case 'take':
      if (!Object.hasOwn(labels, args.deck)) throw Error('Unknown deck');
      stage.take(args.deck); break;
    case 'black': stage.black(); break;
    case 'close': {
      const page = stage.get(args.id);
      stage.remove(page.id);
      page.view.webContents.close();
      break;
    }
    case 'key': {
      if (!['Right', 'Left'].includes(args.key)) throw Error('Unknown key');
      if (stage.active === null) throw Error('目前是黑屏。');
      const wc = stage.get(stage.active).view.webContents;
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
      wc.sendInputEvent({ type: 'keyDown', keyCode: args.key });
      wc.sendInputEvent({ type: 'keyUp', keyCode: args.key });
      break;
    }
    case 'show-output': output.show(); break;
    default: throw Error('Unknown action');
  }
  return stage.snapshot();
}

app.whenReady().then(async () => {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'Sunday Presenter', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }] },
    { role: 'editMenu' },
    { label: 'Presentation', submenu: [
      { label: '顯示 / 隱藏控制台', accelerator: controlShortcut, click: toggleControls },
      { label: '主日崇拜', accelerator: 'CmdOrCtrl+1', click: () => act('take', { deck: 'worship' }).catch(() => {}) },
      { label: '講道', accelerator: 'CmdOrCtrl+2', click: () => act('take', { deck: 'sermon' }).catch(() => {}) },
      { label: '黑屏 / 靜音', accelerator: 'CmdOrCtrl+0', click: () => stage.black() }
    ] }, { role: 'windowMenu' }
  ]));
  officeSession = session.fromPartition(smoke ? 'smoke-office' : 'persist:office');
  const permissionAllowed = (wc, permission) => permission === 'fullscreen' && wc &&
    (stage?.preview === wc.id || stage?.active === wc.id);
  officeSession.setPermissionRequestHandler((wc, permission, callback) => callback(!!permissionAllowed(wc, permission)));
  officeSession.setPermissionCheckHandler((wc, permission) => !!permissionAllowed(wc, permission));
  officeSession.on('will-download', event => event.preventDefault());
  const hostOptions = { width: 1100, height: 660, backgroundColor: '#000000',
    fullscreenable: false, autoHideMenuBar: true };
  output = new BrowserWindow({ ...hostOptions, frame: false, fullscreenable: true, title: 'Sunday Presenter — OUTPUT',
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  await output.loadFile(path.join(__dirname, 'output.html'));
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(Error('Output could not enter fullscreen')), 8000);
    output.once('enter-full-screen', () => { clearTimeout(timeout); resolve(); });
    output.setFullScreen(true);
  });
  preparation = new BaseWindow({ ...hostOptions, frame: false,
    title: 'Sunday Presenter — PRIVATE PREPARATION', show: false });
  controller = new BrowserWindow({ width: 740, height: 850, show: false, alwaysOnTop: true,
    title: 'Sunday Presenter — 控制台',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), sandbox: true,
      contextIsolation: true, nodeIntegration: false } });
  if (process.platform === 'darwin') {
    controller.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    preparation.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }
  preparation.setAlwaysOnTop(true);
  stage = new Stage(output, preparation, changed);
  stage.layout();
  preparation.setPosition(...output.getPosition());
  if (!globalShortcut.register(controlShortcut, toggleControls)) {
    dialog.showErrorBox('無法啟用控制台快捷鍵', '⌘P 已被其他程式使用。請關閉衝突程式後重開 Sunday Presenter。');
    app.quit(); return;
  }
  output.on('resize', () => stage.layout());
  output.on('move', () => preparation.setPosition(...output.getPosition()));
  output.on('close', event => { if (!quitting) event.preventDefault(); });
  preparation.on('close', event => {
    if (!quitting) { event.preventDefault(); preparation.hide(); }
  });
  controller.on('close', event => {
    if (!quitting) { event.preventDefault(); controller.hide(); preparation.hide(); output.focus(); }
  });
  controller.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  controller.webContents.on('will-navigate', event => event.preventDefault());
  ipcMain.handle('presenter:action', async (event, action, args) => {
    if (event.sender !== controller.webContents || event.senderFrame !== controller.webContents.mainFrame ||
        event.senderFrame.url !== controlURL) throw Error('Untrusted caller');
    try { return { state: await act(action, args) }; }
    catch (error) { return { error: error.message }; }
  });
  await controller.loadFile(path.join(__dirname, 'control.html'));
  if (smoke) {
    try {
      await require('./test/smoke.cjs').run({ stage, act, output, preparation, controller, toggleControls,
        setOrigin: origin => { testOrigin = origin; } });
      console.log('SMOKE PASS');
      app.quit();
    } catch (error) { console.error(error); app.exit(1); }
  }
});
app.on('second-instance', () => { output?.show(); });
app.on('before-quit', () => {
  quitting = true;
  globalShortcut.unregisterAll();
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
