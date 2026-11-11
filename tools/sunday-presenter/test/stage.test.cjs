const test = require('node:test');
const assert = require('node:assert/strict');
const { Stage, allowedURL } = require('../stage.cjs');

function host() {
  const children = new Set();
  return { children, contentView: { addChildView: v => children.add(v), removeChildView: v => children.delete(v) },
    getContentSize: () => [1280, 720], setContentSize() {}, show() {}, hide() {} };
}
function view(id) {
  return { visible: false, setVisible(v) { this.visible = v; }, setBounds() {},
    webContents: { id, muted: false, isDestroyed: () => false, isLoading: () => false,
      setAudioMuted(v) { this.muted = v; }, isAudioMuted() { return this.muted; }, focus() {} } };
}
function ready(stage, page) { page.loaded = true; stage.prepare(page.id); stage.arm(page.id); }

test('switches retain views and mute every inactive editor and popup', () => {
  const output = host(), prep = host(), stage = new Stage(output, prep);
  const worship = stage.add('worship', view(1));
  ready(stage, worship);
  const sermon = stage.add('sermon', view(2)); ready(stage, sermon);
  const authPopup = stage.add('worship', view(3));
  // Selecting the verified worship page again does not recreate it.
  ready(stage, worship);
  for (let i = 0; i < 50; i++) {
    const page = i % 2 ? worship : sermon;
    stage.take(page.deck);
    assert.equal(stage.active, page.id);
    for (const other of stage.pages.values()) {
      assert.equal(other.view.webContents.muted, other !== page);
      assert.equal(other.view.visible, other === page);
    }
  }
  assert.equal(stage.get(1).view, worship.view);
  assert.equal(authPopup.view.webContents.muted, true);
  stage.black();
  assert.ok([...stage.pages.values()].every(p => !p.view.visible && p.view.webContents.muted));
});

test('never publishes an uninspected page; preparation, navigation and failure revoke readiness', () => {
  const stage = new Stage(host(), host());
  const page = stage.add('worship', view(1));
  assert.throws(() => stage.take('worship'));
  assert.throws(() => stage.arm(page.id));
  ready(stage, page); stage.take('worship');
  stage.prepare(page.id);
  assert.equal(stage.active, null); assert.equal(page.ready, false);
  stage.arm(page.id); stage.take('worship');
  stage.invalidate(page.id, 'crash');
  assert.equal(stage.active, null); assert.equal(page.view.visible, false);
  assert.throws(() => stage.take('worship'));
  stage.remove(page.id);
  assert.throws(() => stage.get(page.id));
});

test('only HTTPS or the exact isolated smoke origin may navigate', () => {
  assert.ok(allowedURL('https://tenant.sharepoint.com/a.pptx'));
  assert.ok(allowedURL('about:blank'));
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'http://example.com',
    'https://user:pass@example.com', 'ms-powerpoint:ofe|u|https://example.com', 'data:text/html,a']) {
    assert.equal(allowedURL(url), false, url);
  }
  assert.ok(allowedURL('http://127.0.0.1:1234/a', 'http://127.0.0.1:1234'));
  assert.equal(allowedURL('http://127.0.0.1:1235/a', 'http://127.0.0.1:1234'), false);
});

test('background popups cannot steal the chosen audience page or preparation view', () => {
  const stage = new Stage(host(), host());
  const page = stage.add('worship', view(1)); ready(stage, page); stage.take('worship');
  stage.add('worship', view(2), { select: false });
  assert.equal(stage.selected.worship, page.id);
  assert.equal(stage.active, page.id);
  assert.equal(stage.preview, null);
  assert.equal(stage.get(2).view.visible, false);
  assert.equal(stage.get(2).view.webContents.muted, true);
  stage.take('worship'); // still available with one click
});


test('stopping or invalidating hides output; deck switching keeps the window', () => {
  const output = host(); let shown = false, hides = 0;
  output.show = () => { shown = true; }; output.hide = () => { shown = false; hides++; };
  const stage = new Stage(output, host());
  const first = stage.add('worship', view(1)), second = stage.add('sermon', view(2));
  ready(stage, first); ready(stage, second);
  stage.take('worship'); assert.equal(shown, true); assert.equal(hides, 0);
  stage.take('sermon'); assert.equal(shown, true); assert.equal(hides, 0);
  stage.invalidate(second.id, 'navigation'); assert.equal(shown, false);
  assert.equal(stage.active, null); assert.ok([...stage.pages.values()].every(p => p.view.webContents.muted));
});
