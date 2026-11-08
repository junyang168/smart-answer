const { test } = require('node:test');
const assert = require('node:assert/strict');
const { viewDocumentURL, installOfficeView } = require('../office-view.cjs');
const { isDocumentURL } = require('../folder-wizard.js');
const document = 'https://onedrive.live.com/personal/example/_layouts/15/Doc.aspx?sourcedoc=%7Babc%7D&file=Sunday.pptx&action=edit&mobileredirect=true';

test('manual links and Office GET popups use the reader and preserve file identity', () => {
  const before = new URL(document), after = new URL(viewDocumentURL(document));
  assert.equal(after.searchParams.get('action'), 'view');
  for (const key of ['sourcedoc','file','mobileredirect']) assert.equal(after.searchParams.get(key), before.searchParams.get(key));
  assert.equal(viewDocumentURL(after.href), after.href);
  assert.equal(new URL(viewDocumentURL(document.replace('&action=edit',''))).searchParams.get('action'),'view');
  assert.equal(new URL(viewDocumentURL(document.replace('action=edit','Action=Edit&action=edit'))).searchParams.get('action'),'view');
});

test('reader doc2 redirect and SharePoint documents are recognized without redirect loops', () => {
  const reader=document.replace('Doc.aspx','doc2.aspx').replace('action=edit','action=view');
  assert.equal(new URL(viewDocumentURL(reader)).searchParams.get('action'),'view');assert.equal(isDocumentURL(reader),true);
  assert.equal(new URL(viewDocumentURL(document.replace('onedrive.live.com','church.sharepoint.com'))).searchParams.get('action'),'view');
  for(const action of ['view','present']) {
    const value=document.replace('action=edit','action='+action);assert.equal(viewDocumentURL(value),value);
  }
});

test('folder, OAuth, slideshow and lookalike URLs remain untouched', () => {
  for(const value of ['https://onedrive.live.com/my?id=folder','https://login.live.com/oauth20_authorize.srf',
    'https://powerpoint.officeapps.live.com/p/PowerPointFrame.aspx?PowerPointView=SlideShowView',
    'https://onedrive.live.com.evil.test/Doc.aspx?action=edit','https://sharepoint.com.evil.test/Doc.aspx?action=edit',
    'http://onedrive.live.com/Doc.aspx?action=edit','about:blank','not a URL']) assert.equal(viewDocumentURL(value),value);
});

test('session interception redirects only document GET mainframes; POST popup data is preserved', () => {
  let handler;installOfficeView({webRequest:{onBeforeRequest:(filter, callback)=>{handler=callback;assert.equal(filter.urls.length,2);}}});
  function run(overrides={}) { let result;handler({url:document,method:'GET',resourceType:'mainFrame',...overrides},r=>{result=r;});return result; }
  assert.equal(new URL(run().redirectURL).searchParams.get('action'),'view');
  for(const value of [{method:'POST'},{resourceType:'subFrame'},{resourceType:'xhr'},{url:viewDocumentURL(document)}]) assert.deepEqual(run(value),{});
});


test('embedded reader links use the read-only preparation page with a slideshow entry', () => {
  const embed = document.replace('action=edit', 'action=embedview');
  assert.equal(new URL(viewDocumentURL(embed)).searchParams.get('action'), 'view');
});
