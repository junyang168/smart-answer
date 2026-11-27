import test from 'node:test';
import assert from 'node:assert/strict';
import { sermonEditorHref, countByStatus, seriesIdFromRoute } from '../src/app/components/series/progress.ts';

test('active Editor URL wins over the former rough transcript link', () => {
  const first = { item: 'INGEST-first', status: 'assigned', rough_href: '/rough/first', editor_href: '/admin/transcript-beta/INGEST-first' };
  assert.equal(sermonEditorHref(first), first.editor_href);
  assert.equal(sermonEditorHref({ ...first, status: 'processing' }), first.editor_href);
  const remaining = Array.from({length: 41}, () => ({status: 'processing'}));
  assert.deepEqual(countByStatus([first, ...remaining]), {published: 0, assigned: 1, ready: 0, processing: 41});
});

test('unprocessed recordings retain rough entry and existing editors retain their route', () => {
  assert.equal(sermonEditorHref({item: 'first', status: 'processing', rough_href: '/rough/first'}), '/rough/first');
  assert.equal(sermonEditorHref({item: '中文 1', status: 'assigned', rough_href: '/rough/first'}), `/admin/surmons/${encodeURIComponent('中文 1')}`);
});


test('series routes accept raw, encoded and old double-encoded identifiers', () => {
  const id='2012 NYSC 專題：馬太福音釋經（三）';
  for (const route of [id,encodeURIComponent(id),encodeURIComponent(encodeURIComponent(id))]) {
    assert.equal(seriesIdFromRoute(route),id);
  }
  assert.equal(seriesIdFromRoute('50% 的講道'),'50% 的講道');
});
