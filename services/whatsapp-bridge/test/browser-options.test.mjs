import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { browserOptions } from '../browser-options.mjs';

test('macOS agent cannot fall back to desktop Chrome or an absent binary', () => {
  assert.throws(() => browserOptions({},'darwin'), /standalone/);
  assert.throws(() => browserOptions({SUNDAY_WHATSAPP_CHROME:'/missing/chrome-headless-shell'},'darwin'), /installed/);
  const root=mkdtempSync(join(tmpdir(),'browser-options-'));
  try {
    const desktop=join(root,'Google Chrome');writeFileSync(desktop,'desktop');
    assert.throws(() => browserOptions({SUNDAY_WHATSAPP_CHROME:desktop},'darwin'), /desktop Chrome/);
    const shell=join(root,'chrome-headless-shell');writeFileSync(shell,'shell');
    assert.deepEqual(browserOptions({SUNDAY_WHATSAPP_CHROME:shell},'darwin'),{headless:'shell',executablePath:shell});
    assert.deepEqual(browserOptions({SUNDAY_WHATSAPP_CHROME:desktop},'linux'),{headless:true,executablePath:desktop});
  } finally {rmSync(root,{recursive:true,force:true});}
});
