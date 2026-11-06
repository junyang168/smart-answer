const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {execFileSync} = require('node:child_process');
const root = path.join(__dirname, '..');
test('actual Mac UI command path obeys observer, pairing and live-state gates', {skip:process.platform !== 'darwin'}, () => {
  execFileSync(process.execPath, [path.join(root, 'build-native.cjs')], {stdio:'pipe'});
  const app = path.join(root, 'output/native/Sunday Remote.app');
  const result = JSON.parse(execFileSync(path.join(app, 'Contents/MacOS/Sunday Remote'), ['--scope-probe'], {encoding:'utf8'}));
  for (const [name, passed] of Object.entries(result)) assert.equal(passed, true, name);
  assert.equal(Object.keys(result).length, 6);
  const plist = JSON.parse(execFileSync('plutil', ['-convert','json','-o','-',path.join(app,'Contents/Info.plist')], {encoding:'utf8'}));
  assert.equal(plist.CFBundleShortVersionString, require('../package.json').version);
  assert.equal(plist.CFBundleVersion, require('../package.json').version);
});
test('iPad project obtains both version fields from the same release as Mac', () => {
  execFileSync(process.execPath, [path.join(root, 'ipad/generate-project.cjs')]);
  const project = fs.readFileSync(path.join(root, 'ipad/SundayRemote.xcodeproj/project.pbxproj'), 'utf8');
  const version = require('../package.json').version;
  for (const field of ['CURRENT_PROJECT_VERSION', 'MARKETING_VERSION']) {
    const values = [...project.matchAll(new RegExp(`${field} = ([0-9.]+);`, 'g'))].map(m => m[1]);
    assert.deepEqual(values, [version, version]);
  }
});
test('built Mac Remote verifies the actual TLS pin and refuses HTTP redirects', {skip:process.platform !== 'darwin'}, async t => {
  const os = require('node:os');
  const https = require('node:https');
  const {identity} = require('../remote-identity.cjs');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(),'sunday-mac-pin-'));
  const host = identity(temp), servers = [];
  t.after(async () => {
    await Promise.all(servers.map(server => new Promise(resolve => server.close(resolve))));
    fs.rmSync(temp,{recursive:true,force:true});
  });
  let destinationRequests = 0;
  const destination = https.createServer(host.tls, (_req,res) => {
    destinationRequests++; res.end(JSON.stringify({live:true,binding:'synthetic'}));
  });
  servers.push(destination);
  await new Promise(resolve => destination.listen(0,'127.0.0.1',resolve));
  const redirect = https.createServer(host.tls, (_req,res) => {
    res.writeHead(302,{Location:`https://127.0.0.1:${destination.address().port}/state`}); res.end();
  });
  servers.push(redirect);
  await new Promise(resolve => redirect.listen(0,'127.0.0.1',resolve));
  const binary = path.join(root,'output/native/Sunday Remote.app/Contents/MacOS/Sunday Remote');
  async function probe(port, fingerprint) {
    const child = require('node:child_process').spawn(binary,['--probe'],{stdio:['pipe','pipe','pipe']});
    child.stdin.end(JSON.stringify({url:`https://127.0.0.1:${port}`,fingerprint,token:'A'.repeat(43)})+'\n');
    let stdout = ''; child.stdout.on('data',data => { stdout += data; });
    child.stderr.resume();
    const timer = setTimeout(() => child.kill(),8000);
    const code = await new Promise((resolve,reject) => { child.on('error',reject); child.on('close',resolve); });
    clearTimeout(timer);
    return {code,response:JSON.parse(stdout)};
  }
  const accepted = await probe(destination.address().port,host.id);
  assert.equal(accepted.code,0); assert.equal(accepted.response.ok,true);
  assert.equal(destinationRequests,1);
  const wrong = await probe(destination.address().port,'b'.repeat(64));
  assert.notEqual(wrong.code,0); assert.equal(wrong.response.ok,false);
  const moved = await probe(redirect.address().port,host.id);
  assert.notEqual(moved.code,0); assert.equal(moved.response.ok,false);
  assert.equal(destinationRequests,1,'redirect must never reach another endpoint');
});
