const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const https = require('node:https');
const { spawn } = require('node:child_process');
const { RemoteControl } = require('../remote.cjs');
const { identity } = require('../remote-identity.cjs');
exports.run = async ({ hostVideo, stage, act, output, worship, sermon, evidence }) => {
  const temp = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'sunday-video-smoke-'));
  const source = identity(temp);
  const remote = new RemoteControl({ identity: source, advertise: () => () => {}, video: hostVideo,
    state: () => stage.snapshot(), key: key => act('key', { key }) });
  const pair = () => new Promise((resolve, reject) => {
    const req = https.request({ hostname: '127.0.0.1', port: remote.server.address().port, path: '/pair',
      method: 'POST', ca: source.tls.cert,
      checkServerIdentity: (_host, cert) => cert.fingerprint256.replaceAll(':', '').toLowerCase() === source.id ? undefined : Error('Wrong host'),
      headers: { 'Content-Type': 'application/json' } }, res => {
        let text = ''; res.on('data', chunk => { text += chunk; }); res.on('end', () => resolve(JSON.parse(text)));
      }); req.on('error', reject); req.end(JSON.stringify({ code: remote.snapshot().code }));
  });
  let child, childError, latest, samples = [], buffer = '', screenshot = false, layouts = [];
  const wait = async (check, label, ms = 30000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      if (childError) throw Error(childError);
      if (await check()) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    console.log('video debug:', { host: await hostVideo.stats(), signaling: hostVideo.sessions.current && { status: hostVideo.sessions.current.status, error: hostVideo.sessions.current.error }, latest });
    throw Error('Video timeout: ' + label);
  };
  const sample = async () => {
    const n = samples.length; child.stdin.write('sample\n'); await wait(() => samples.length > n, 'sample'); return samples.at(-1);
  };
  try {
    for (const [page, color] of [[worship, '#123c50'], [sermon, '#703018']]) await page.view.webContents.executeJavaScript(`document.body.style.background=${JSON.stringify(color)}`);
    await act('take', { deck: 'sermon' }); await remote.start({ host: '127.0.0.1' });
    const paired = await pair(); assert.ok(paired.token);
    child = spawn(path.join(__dirname, '../output/native/Sunday Remote.app/Contents/MacOS/Sunday Remote'), ['--video-probe'], { stdio: ['pipe', 'pipe', 'pipe'] });
    child.once('error', error => { childError = error.message; }); child.once('exit', code => { childError = `Native viewer exited (${code})`; });
    child.stderr.on('data', chunk => console.log('WebKit:', chunk.toString().slice(0, 1000)));
    child.stdout.on('data', chunk => {
      buffer += chunk.toString(); let i;
      while ((i = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, i); buffer = buffer.slice(i + 1);
        try {
          const value = JSON.parse(line);
          if (value.op === 'layout') layouts.push(value);
          if (value.op === 'stats') latest = value;
          if (value.op === 'sample') samples.push(value.value);
          if (value.op === 'snapshot') screenshot = value.ok;
          if (['diagnostic','diagnose'].includes(value.op)) console.log('viewer:', value);
        } catch { console.log('Native:', line.slice(0, 300)); }
      }
    });
    child.stdin.write(JSON.stringify({ url: `https://127.0.0.1:${remote.server.address().port}`, fingerprint: source.id, token: paired.token,
      snapshot: path.join(evidence, 'remote-video.png') }) + '\n');
    const diagnoseTimer = setTimeout(() => child.stdin.write('diagnose\n'), 12000); diagnoseTimer.unref();
    await wait(() => latest?.frames > 10, 'WebKit decodes captured frames', 45000);
    clearTimeout(diagnoseTimer);
    assert.equal(latest.audio, false); assert.ok(latest.width > 0 && latest.height > 0);
    assert.ok(latest.candidates.length); assert.ok(latest.candidates.every(c => c.local === 'host' && c.remote === 'host'));
    const firstSession = latest.sessionId, sermonPixel = (await sample()).pixel;
    assert.ok(sermonPixel[0] > sermonPixel[2] + 40, JSON.stringify(sermonPixel));
    console.log('video: WebKit decoded actual OUTPUT', { ...latest, pixel: sermonPixel });
    if (process.argv.includes('--video-shutdown-smoke')) {
      await hostVideo.close();
      assert.equal((await hostVideo.stats()).state, 'stopped');
      assert.throws(() => hostVideo.request({ op: 'start', clientId: 'test-viewer-12345' }), /stopped/);
      console.log('VIDEO ACTIVE SHUTDOWN PASS'); return;
    }
    const layout = async () => {
      const count = layouts.length; child.stdin.write('layout\n');
      await wait(() => layouts.length > count, 'native layout'); return layouts.at(-1);
    };
    const normalLayout = await layout();
    assert.ok(normalLayout.viewerHeight / normalLayout.height > 0.85, JSON.stringify(normalLayout));
    assert.ok(normalLayout.controlsTop <= normalLayout.viewerBottom);
    assert.equal(normalLayout.awake, true);
    child.stdin.write('fullscreen\n');
    await wait(async () => (await layout()).fullscreen && layouts.at(-1).fullscreenTitle === '退出全螢幕', 'native fullscreen enters');
    assert.ok(latest.frames > 0);
    child.stdin.write('fullscreen\n');
    await wait(async () => !(await layout()).fullscreen && layouts.at(-1).fullscreenTitle === '全螢幕', 'native fullscreen exits');
    if (process.argv.includes('--video-idle-smoke')) {
      // Leave the still slide and client untouched for three real minutes.
      const until = Date.now() + 180000;
      while (Date.now() < until) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (childError) throw Error(childError);
        assert.equal(hostVideo.sessions.current?.id, firstSession);
      }
      assert.equal(latest.sessionId, firstSession);
      assert.equal((await layout()).awake, true);
      console.log('VIDEO UNTOUCHED 180 SECOND PASS');
    }
    const steps = await sermon.view.webContents.executeJavaScript('steps'); child.stdin.write('right-button\n');
    await wait(async () => await sermon.view.webContents.executeJavaScript('steps') === steps + 1, 'native Remote button');
    child.stdin.write('right-viewer\n');
    await wait(async () => await sermon.view.webContents.executeJavaScript('steps') === steps + 2, 'native arrow with WebKit focused');
    for (let i = 0; i < 20; i++) { await act('take', { deck: i % 2 ? 'sermon' : 'worship' }); await new Promise(resolve => setTimeout(resolve, 100)); }
    await act('take', { deck: 'worship' });
    let pixel;
    await wait(async () => { pixel = (await sample()).pixel; return pixel[2] > pixel[0] + 40; }, 'switch changes received pixels');
    assert.equal(latest.sessionId, firstSession);
    const stats = await hostVideo.stats(); assert.deepEqual(stats.tracks, ['video']);
    child.stdin.write('snapshot\n'); await wait(() => screenshot, 'native viewer screenshot');
    await act('prepare', { id: worship.id });
    await wait(async () => (await hostVideo.stats()).state === 'stopped', 'preparation stops capture');
    await wait(async () => !(await sample()).visible, 'private preparation hidden in Remote');
    await act('arm', { id: worship.id }); await act('take', { deck: 'worship' });
    await wait(() => latest?.sessionId !== firstSession && latest?.frames > 5, 'source confirmation reconnects');
    const secondSession = latest.sessionId; child.stdin.write('reconnect\n');
    await wait(() => latest?.sessionId !== secondSession && latest?.frames > 5, 'viewer reconnects');
    output.minimize(); await wait(async () => (await hostVideo.stats()).state === 'stopped', 'minimized output stops capture'); output.restore();
    const result = { date: new Date().toISOString(), localSingleMac: true, electron: process.versions.electron,
      macOS: require('node:child_process').execFileSync('sw_vers', ['-productVersion'], { encoding: 'utf8' }).trim(),
      nativeWebKitDecodedFrames: true, audioTracks: 0, selectedCandidates: latest.candidates,
      colors: { sermon: sermonPixel, worship: pixel }, stableSessionAcross20DeckSwitches: true,
      compactBottomControls: true, nativeFullscreenRoundTrip: true, idleAwakeAssertion: true,
      untouchedSeconds: process.argv.includes('--video-idle-smoke') ? 180 : 0,
      nativeSlideControl: true, nativeButton: true, nativeArrowWithViewerFocused: true, preparationStopsCapture: true, sourceRecovery: true, viewerReconnect: true,
      minimizedStopsCapture: true, host: stats, physicalSecondMacAndHotspot: 'NOT TESTED', realOfficePPT: 'NOT TESTED' };
    fs.writeFileSync(path.join(evidence, 'video-result.json'), JSON.stringify(result, null, 2)); console.log('VIDEO SMOKE PASS', result);
  } finally { child?.stdin.end('quit\n'); child?.kill(); await hostVideo.halt(); await remote.stop(); fs.rmSync(temp, { recursive: true, force: true }); }
};
