const { test } = require('node:test');
const assert = require('node:assert/strict');
const { identifyFiles, isDocumentURL, sameFolderURL, cardPage } = require('../folder-wizard.js');
test('only an unambiguous worship and sole remaining PPT are assigned', () => {
  assert.deepEqual(identifyFiles(['主日崇拜-2026-11-08-自動生成.pptx', '喜樂的感恩.pptx', '~$temp.pptx', 'readme.txt']),
    { worship: '主日崇拜-2026-11-08-自動生成.pptx', sermon: '喜樂的感恩.pptx' });
  for (const files of [[], ['a.pptx'], ['a.pptx','b.pptx'], ['主日崇拜.pptx'],
    ['主日崇拜.pptx','sermon.pptx','old.pptx'], ['主日崇拜.pptx','主日崇拜-final.pptx','sermon.pptx']]) assert.equal(identifyFiles(files), null);
});
test('auto-follow is restricted to Microsoft document pages, never OAuth or lookalikes', () => {
  assert.equal(isDocumentURL('https://onedrive.live.com/personal/user/_layouts/15/Doc.aspx?file=a.pptx'),true);
  for (const url of ['https://login.live.com/oauth', 'https://onedrive.live.com/my?id=folder', 'about:blank',
    'https://onedrive.live.com.evil.test/Doc.aspx', 'https://sharepoint.com.evil.test/Doc.aspx']) assert.equal(isDocumentURL(url), false);
});
test('the confirmed deck remains the main card despite old duplicate popup records', () => {
  const pages = [{id:1,deck:'worship',title:'folder'}, {id:2,deck:'worship',title:'a.pptx',ready:true},
    {id:3,deck:'worship',title:'a.pptx',ready:false}];
  const state = {pages,selected:{worship:2},preview:null};
  assert.equal(cardPage(state,'worship').id,2);
  state.preview=3;assert.equal(cardPage(state,'worship').id,3);
  state.preview=null;state.selected.worship=1;pages[1].ready=false;
  assert.equal(cardPage(state,'worship').id,3);
});

test('OneDrive date encoding redirects keep the same folder identity', () => {
  assert.equal(sameFolderURL('https://onedrive.live.com/my?id=%2Fpersonal%2Fa%2F11%2D8%2D2026', 'https://onedrive.live.com/my?id=%2Fpersonal%2Fa%2F11-8-2026'),true);
  assert.equal(sameFolderURL('https://onedrive.live.com/my?id=/personal/a/11-1-2026', 'https://onedrive.live.com/my?id=/personal/a/11-8-2026'),false);
});
