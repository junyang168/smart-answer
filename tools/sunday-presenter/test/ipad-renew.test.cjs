const test = require('node:test');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
test('renewal preserves expiry on failure and guards locked-device installation', () => {
  execFileSync(process.platform === 'darwin' ? '/usr/bin/python3' : 'python3',
    [path.join(__dirname, '../ipad/test_renew.py')], {encoding: 'utf8', timeout: 20000});
});
