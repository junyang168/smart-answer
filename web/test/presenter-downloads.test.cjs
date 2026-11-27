const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), vm = require('node:vm');
const crypto = require('node:crypto'), ts = require('typescript');
function moduleAt(file, overrides = {}) {
  const result = {};
  const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/app/', file), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText;
  vm.runInNewContext(compiled, { exports: result, process, ReadableStream, require: name => name in overrides ? overrides[name] : require(name) });
  return result;
}
const downloads = moduleAt('utils/sunday-presenter-downloads.ts');
let session = null;
const authorize = moduleAt('api/admin/sunday-presenter/authorize.ts', {
  '@/app/utils/auth': { getSessionWithDevFallback: async () => session },
  '@/app/utils/roles': { hasEditorAccess: role => ['admin', 'editor', 'beta_tester'].includes(role) },
});
const overrides = { '@/app/utils/sunday-presenter-downloads': downloads, './authorize': authorize, '../../authorize': authorize };
const metadata = moduleAt('api/admin/sunday-presenter/route.ts', overrides);
const download = moduleAt('api/admin/sunday-presenter/download/[asset]/route.ts', overrides);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'presenter-downloads-'));
const root = path.join(temporary, 'sunday-presenter/releases'), versionDir = path.join(root, '0.3.0');
fs.mkdirSync(versionDir, { recursive: true });
const manifest = { schemaVersion: 1, version: '0.3.0', status: 'pilot', artifacts: Object.entries(downloads.presenterAssets).map(([id, spec]) => {
  const filename = spec.prefix + '0.3.0' + spec.suffix, bytes = Buffer.from('binary fixture ' + id);
  fs.writeFileSync(path.join(versionDir, filename), bytes);
  return { id, filename, bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex') };
}) };
function save(value = manifest) { fs.writeFileSync(path.join(root, 'latest.json'), JSON.stringify(value)); }
save();
const oldBase = process.env.DATA_BASE_DIR;
test.before(() => { process.env.DATA_BASE_DIR = temporary; });
test.after(() => { if (oldBase === undefined) delete process.env.DATA_BASE_DIR; else process.env.DATA_BASE_DIR = oldBase; fs.rmSync(temporary, { recursive: true }); });
test('authentication is checked in both routes before accessing any release', async () => {
  const base = process.env.DATA_BASE_DIR; delete process.env.DATA_BASE_DIR;
  try {
    for (const [value, status] of [[null, 401], [{ user: { email: 'reader@example.test', role: 'reader' } }, 403]]) {
      session = value;
      assert.equal((await metadata.GET()).status, status);
      assert.equal((await download.GET(null, { params: Promise.resolve({ asset: 'presenter-mac-arm64' }) })).status, status);
    }
  } finally { process.env.DATA_BASE_DIR = base; }
});
test('editors receive only public release metadata and exact streamed attachment bytes', async () => {
  session = { user: { email: 'editor@example.test', role: 'editor' } };
  const response = await metadata.GET(), body = await response.json();
  assert.equal(response.status, 200); assert.equal(body.version, '0.3.0'); assert.equal(body.artifacts.length, 2);
  assert.ok(!JSON.stringify(body).includes(temporary)); assert.equal(response.headers.get('cache-control'), 'private, no-store');
  for (const artifact of manifest.artifacts) {
    const result = await download.GET(null, { params: Promise.resolve({ asset: artifact.id }) });
    assert.equal(result.status, 200); assert.match(result.headers.get('content-disposition'), /attachment/);
    assert.equal(crypto.createHash('sha256').update(Buffer.from(await result.arrayBuffer())).digest('hex'), artifact.sha256);
  }
  assert.equal((await download.GET(null, { params: Promise.resolve({ asset: '../latest.json' }) })).status, 404);
});
test('released Phase 1 metadata is served while unknown release statuses fail closed', async () => {
  save({...manifest, status:'released'});
  const result = await downloads.loadPresenterRelease();
  assert.equal(result.status, 'released');
  assert.equal((await (await metadata.GET()).json()).status, 'released');
  save({...manifest, status:'unchecked'});
  await assert.rejects(downloads.loadPresenterRelease());
  save();
});
test('invalid manifests, missing bytes and symlink escape fail closed', async () => {
  for (const change of [m => { m.version = '../outside'; }, m => { m.artifacts[0].filename = '../outside'; },
    m => { m.artifacts[0].bytes++; }, m => { m.artifacts[0].sha256 = 'bad'; }]) {
    const copy = JSON.parse(JSON.stringify(manifest)); change(copy); save(copy);
    await assert.rejects(downloads.loadPresenterRelease());
  }
  save();
  const file = path.join(versionDir, manifest.artifacts[0].filename), original = fs.readFileSync(file);
  fs.unlinkSync(file); fs.symlinkSync(path.join(root, 'latest.json'), file);
  await assert.rejects(downloads.loadPresenterRelease()); fs.unlinkSync(file); fs.writeFileSync(file, original);
  fs.unlinkSync(file); assert.equal((await metadata.GET()).status, 503); fs.writeFileSync(file, original);
});
test('installer routes stay in Next before the broad backend rewrite', async () => {
  const factory = (await import('../next.config.mjs')).default;
  const rules = await factory('', { defaultConfig: {} }).rewrites();
  const local = rules.findIndex(rule => rule.source === '/api/admin/sunday-presenter/:path*');
  const fallback = rules.findIndex(rule => rule.source === '/api/:path((?!auth|sc_api).*)');
  assert.ok(local >= 0 && local < fallback);
  assert.equal(rules[local].destination, rules[local].source);
});
