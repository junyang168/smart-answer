const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

test('native Meet locator skips large unrelated pages and rejects the wrong room', { skip: process.platform !== 'darwin' }, () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'sunday-meet-locator-'));
  try {
    const source = fs.readFileSync(path.join(__dirname, '../native/MeetWindowLocator.swift'), 'utf8') + '\n' +
      fs.readFileSync(path.join(__dirname, 'meet-window-locator.swift'), 'utf8');
    const main = path.join(directory, 'main.swift'), binary = path.join(directory, 'test');
    fs.writeFileSync(main, source);
    execFileSync('/usr/bin/swiftc', ['-swift-version', '5', main, '-o', binary], { timeout: 60000 });
    assert.match(execFileSync(binary, { encoding: 'utf8', timeout: 5000 }), /counterexamples passed/);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
