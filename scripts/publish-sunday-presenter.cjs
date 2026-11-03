#!/usr/bin/env node
// Install reviewed binaries outside Git; switch latest.json only after readback.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
async function hash(file) {
  const value = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) value.update(chunk);
  return value.digest('hex');
}
async function main() {
  const args = process.argv.slice(2), options = {};
  while (args.length) {
    const flag = args.shift();
    if (!['--root', '--dist', '--version'].includes(flag) || !args.length) throw Error('Usage: publish-sunday-presenter.cjs --version <version> --root <data>/sunday-presenter/releases [--dist <build-dir>]');
    options[flag] = args.shift();
  }
  if (!options['--root']) throw Error('--root is required');
  if (!options['--version']) throw Error('--version is required');
  const source = path.resolve(__dirname, '../tools/sunday-presenter');
  const version = options['--version'];
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw Error('Invalid version');
  const dist = path.resolve(options['--dist'] || path.join(source, 'dist'));
  const definitions = [['presenter-mac-arm64', `Sunday-Presenter-${version}-mac-arm64.dmg`],
    ['remote-mac-universal', `Sunday-Remote-${version}-mac-universal.zip`]];
  const artifacts = [];
  for (const [id, filename] of definitions) {
    const file = path.join(dist, filename), stat = fs.lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || !stat.size) throw Error('Missing regular artifact: ' + filename);
    artifacts.push({ id, filename, bytes: stat.size, sha256: await hash(file) });
  }
  fs.mkdirSync(options['--root'], { recursive: true });
  const root = fs.realpathSync(options['--root']);
  const target = path.join(root, version);
  const temporary = path.join(root, '.stage-' + crypto.randomUUID());
  fs.mkdirSync(temporary);
  try {
    for (const artifact of artifacts) {
      const file = path.join(temporary, artifact.filename);
      fs.copyFileSync(path.join(dist, artifact.filename), file);
      if (await hash(file) !== artifact.sha256) throw Error('Copy readback failed');
    }
    if (fs.existsSync(target)) {
      if (fs.lstatSync(target).isSymbolicLink()) throw Error('Release directory cannot be a symlink');
      for (const artifact of artifacts) {
        const file = path.join(target, artifact.filename);
        if (fs.lstatSync(file).isSymbolicLink() || await hash(file) !== artifact.sha256) throw Error('Version already exists with different bytes; use a new version');
      }
    } else fs.renameSync(temporary, target);
    const manifest = { schemaVersion: 1, version, status: 'pilot', artifacts };
    const next = path.join(root, '.latest-' + crypto.randomUUID());
    fs.writeFileSync(next, JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
    fs.renameSync(next, path.join(root, 'latest.json'));
    console.log(JSON.stringify(manifest, null, 2));
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
