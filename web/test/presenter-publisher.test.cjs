const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync } = require('node:child_process');
test('installer publication is immutable and a failed replacement leaves latest intact', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'presenter-publisher-'));
  const dist = path.join(directory, 'dist'), root = path.join(directory, 'releases');
  fs.mkdirSync(dist);
  const presenter = 'Sunday-Presenter-0.3.1-mac-arm64.dmg';
  const remote = 'Sunday-Remote-0.3.1-mac-universal.zip';
  fs.writeFileSync(path.join(dist, presenter), 'reviewed presenter bytes');
  fs.writeFileSync(path.join(dist, remote), 'reviewed remote bytes');
  const run = (...extra) => spawnSync(process.execPath, [path.join(__dirname, '../../scripts/publish-sunday-presenter.cjs'),
    '--version', '0.3.1', '--root', root, '--dist', dist, ...extra], { encoding: 'utf8' });
  try {
    assert.equal(run().status, 0);
    const manifest = fs.readFileSync(path.join(root, 'latest.json'), 'utf8');
    assert.equal(run().status, 0);
    assert.equal(fs.readFileSync(path.join(root, 'latest.json'), 'utf8'), manifest);
    assert.equal(run('--status','released').status, 0);
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'latest.json'))).status, 'released');
    assert.notEqual(run('--status','unchecked').status, 0);
    assert.equal(run().status, 0);
    fs.writeFileSync(path.join(dist, presenter), 'different build under same version');
    const refused = run(); assert.notEqual(refused.status, 0); assert.match(refused.stderr, /different bytes/);
    assert.equal(fs.readFileSync(path.join(root, 'latest.json'), 'utf8'), manifest);
    assert.equal(fs.readFileSync(path.join(root, '0.3.1', presenter), 'utf8'), 'reviewed presenter bytes');
    assert.ok(!fs.readdirSync(root).some(name => name.startsWith('.stage-')));
  } finally { fs.rmSync(directory, { recursive: true }); }
});
