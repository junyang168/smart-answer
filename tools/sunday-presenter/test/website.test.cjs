const test = require('node:test');
const assert = require('node:assert/strict');
const { Website, comingSunday, validateFolder } = require('../website.cjs');
const now = new Date('2026-11-02T16:00:00Z');
const data = { date: '2026-11-08', timezone: 'America/Chicago', folderName: '11-8-2026',
  folderUrl: 'https://onedrive.live.com/my?id=%2Fpersonal%2Ffixture%2FDocuments%2F%E4%B8%BB%E6%97%A5%2F11-8-2026', folderExists: true };

test('coming Sunday uses Chicago calendar across Sunday, DST and year rollover', () => {
  for (const [stamp, expected] of [
    ['2026-11-02T05:59:59Z', '2026-11-01'], ['2026-11-02T06:00:00Z', '2026-11-08'],
    ['2026-03-08T08:00:00Z', '2026-03-08'], ['2026-12-28T12:00:00Z', '2027-01-03']
  ]) assert.equal(comingSunday(new Date(stamp)), expected);
});

test('rejects stale dates, wrong dated folder and non-OneDrive links', () => {
  assert.equal(validateFolder(data, now).folderName, '11-8-2026');
  for (const changes of [{ date: '2026-11-01' }, { timezone: 'UTC' }, { folderName: '11-1-2026' },
    { folderUrl: data.folderUrl.replace('11-8-2026', '11-1-2026') },
    { folderUrl: 'https://evil.example/my?id=/personal/a/11-8-2026' },
    { folderUrl: 'file:///tmp/11-8-2026' }]) assert.throws(() => validateFolder({ ...data, ...changes }, now));
});

test('refresh coalesces calls and clears old links when offline instead of reusing last week', async () => {
  let calls = 0, fail = false;
  const site = new Website({ now: () => now, fetcher: async (_url, options) => {
    calls++; assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
    if (fail) throw Error('offline');
    return { ok: true, json: async () => data };
  } });
  await Promise.all([site.refresh(), site.refresh()]);
  assert.equal(calls, 1); assert.equal(site.state.folder.date, '2026-11-08');
  fail = true;
  await assert.rejects(site.refresh(), /無法連線/);
  assert.equal(site.state.folder, null); assert.equal(site.state.status, 'error');
  fail = false; await site.refresh(); assert.equal(site.state.status, 'ready');
});

test('refuses insecure external API endpoints and does not follow sign-in redirects', async () => {
  assert.throws(() => new Website({ apiURL: 'http://evil.example/api' }));
  new Website({ apiURL: 'http://127.0.0.1:9590/api' });
  const site = new Website({ fetcher: async () => ({ ok: false, status: 503 }) });
  await assert.rejects(site.refresh(), /503/);
  assert.equal(site.state.folder, null);
});
